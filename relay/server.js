import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8788);
const BOARD_REPO = (process.env.BOARD_REPO ?? "mrlaloo/Railway-GPT-Bridge").trim();
const XAI_MODEL = (process.env.XAI_MODEL ?? "grok-4.6").trim();
const POLL_SECONDS = clampNumber(process.env.POLL_SECONDS, 30, 10, 300);
const BOOT_LOOKBACK_MINUTES = clampNumber(process.env.BOOT_LOOKBACK_MINUTES, 1440, 1, 10080);
const MAX_CONTEXT_COMMENTS = clampNumber(process.env.MAX_CONTEXT_COMMENTS, 16, 2, 60);
const MAX_INPUT_CHARS = clampNumber(process.env.MAX_INPUT_CHARS, 50000, 4000, 200000);
const MAX_OUTPUT_TOKENS = clampNumber(process.env.MAX_OUTPUT_TOKENS, 1800, 200, 8000);
const OVERLAP_SECONDS = clampNumber(process.env.OVERLAP_SECONDS, 120, 10, 600);

const [OWNER, REPO] = BOARD_REPO.split("/");
if (!OWNER || !REPO) throw new Error("BOARD_REPO must be in owner/repo form.");

let sinceIso = new Date(Date.now() - BOOT_LOOKBACK_MINUTES * 60_000).toISOString();
let polling = false;
const inFlight = new Set();

function clampNumber(raw, fallback, min, max) {
  const value = Number(raw ?? fallback);
  return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
}

function config() {
  return {
    githubToken: process.env.GITHUB_TOKEN?.trim() ?? "",
    xaiApiKey: process.env.XAI_API_KEY?.trim() ?? "",
    adminSecret: process.env.RELAY_ADMIN_SECRET?.trim() ?? "",
  };
}

function configurationStatus() {
  const c = config();
  return {
    ready: Boolean(c.githubToken && c.xaiApiKey),
    github_token: Boolean(c.githubToken),
    xai_api_key: Boolean(c.xaiApiKey),
    board_repo: BOARD_REPO,
    model: XAI_MODEL,
    poll_seconds: POLL_SECONDS,
  };
}

function redactSecrets(value) {
  let text = String(value ?? "");
  const patterns = [
    /\bxai-[A-Za-z0-9_-]{10,}\b/g,
    /\bgithub_pat_[A-Za-z0-9_]{10,}\b/g,
    /\bgh[pousr]_[A-Za-z0-9]{10,}\b/g,
    /\bsk-[A-Za-z0-9_-]{16,}\b/g,
    /\bBearer\s+[A-Za-z0-9._~+\/-]{12,}\b/gi,
  ];
  for (const pattern of patterns) text = text.replace(pattern, "[REDACTED_SECRET]");
  text = text.replace(
    /\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)\s*[:=]\s*["']?([^\s"',}]+)/gi,
    "$1=[REDACTED_SECRET]"
  );
  return text;
}

function isGrokRelayComment(body) {
  return /<!--\s*ai-board:provider=grok\b/i.test(String(body ?? ""));
}

function isNoRelayComment(body) {
  return /<!--\s*ai-board:no-relay\s*-->/i.test(String(body ?? ""));
}

function sourceMarker(commentId) {
  return `source-comment-id=${commentId}`;
}

async function githubApi(path, { method = "GET", body } = {}) {
  const { githubToken } = config();
  if (!githubToken) throw new Error("GITHUB_TOKEN is not configured.");

  const response = await fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${githubToken}`,
      "Content-Type": "application/json",
      "User-Agent": "mrlaloo-ai-board-grok-relay/0.1",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`GitHub API ${response.status}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function listRecentRepoComments(since) {
  const results = [];
  for (let page = 1; page <= 5; page += 1) {
    const params = new URLSearchParams({
      since,
      sort: "updated",
      direction: "asc",
      per_page: "100",
      page: String(page),
    });
    const batch = await githubApi(`/repos/${OWNER}/${REPO}/issues/comments?${params}`);
    results.push(...batch);
    if (batch.length < 100) break;
  }
  return results.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
}

async function fetchIssue(issueNumber) {
  return githubApi(`/repos/${OWNER}/${REPO}/issues/${issueNumber}`);
}

async function fetchIssueComments(issueNumber) {
  const results = [];
  for (let page = 1; page <= 5; page += 1) {
    const batch = await githubApi(
      `/repos/${OWNER}/${REPO}/issues/${issueNumber}/comments?per_page=100&page=${page}`
    );
    results.push(...batch);
    if (batch.length < 100) break;
  }
  return results;
}

function alreadyAnswered(comments, sourceCommentId) {
  const marker = sourceMarker(sourceCommentId);
  return comments.some(
    (comment) => isGrokRelayComment(comment.body) && String(comment.body ?? "").includes(marker)
  );
}

function compactContext(issue, comments, sourceComment) {
  const sourceTime = new Date(sourceComment.created_at).getTime();
  const eligible = comments
    .filter((comment) => new Date(comment.created_at).getTime() <= sourceTime)
    .slice(-MAX_CONTEXT_COMMENTS);

  const thread = eligible
    .map((comment) => {
      const provider = isGrokRelayComment(comment.body) ? "GROK" : "BOARD";
      const author = comment.user?.login ?? "unknown";
      return `[${provider} | ${author} | ${comment.created_at}]\n${redactSecrets(comment.body)}`;
    })
    .join("\n\n---\n\n");

  const prompt = `You are Grok participating in a shared engineering discussion board with a human owner and an OpenAI reviewer. The board covers Alfred (Alpaca crypto/grid), MoneyPenny (OANDA FX), Dark Venus (Alpaca stocks), and future automation projects.\n\nOperating rules:\n- Treat the GitHub issue and comments as the shared system of record.\n- Analyze independently, then engage with prior reasoning when present.\n- Do not claim you executed a deploy, trade, commit, or external action unless the evidence explicitly proves it.\n- Do not request or expose broker credentials, API keys, tokens, account IDs, or secrets.\n- The board recommends and reviews; it does not autonomously authorize live trading changes.\n- Distinguish observed evidence from inference.\n- If evidence is insufficient, say exactly what is missing.\n- If the latest message is social/coordination rather than an engineering question, reply naturally and briefly instead of forcing a formal template.\n- For substantive engineering replies, prefer: POSITION, ANALYSIS, EVIDENCE, RISKS, RECOMMENDED NEXT STEP, BLOCKING QUESTIONS.\n- Keep the response useful and compact.\n\nISSUE #${issue.number}: ${redactSecrets(issue.title)}\n\nISSUE BODY:\n${redactSecrets(issue.body ?? "(none)")}\n\nDISCUSSION THROUGH THE MESSAGE YOU ARE ANSWERING:\n${thread || "(no earlier comments)"}\n\nLATEST SOURCE COMMENT ID: ${sourceComment.id}\nRespond to the latest source comment as Grok. Do not include hidden HTML relay markers; the relay adds those itself.`;

  if (prompt.length <= MAX_INPUT_CHARS) return prompt;
  return `${prompt.slice(0, MAX_INPUT_CHARS - 500)}\n\n[CONTEXT TRUNCATED BY RELAY]\n\nLATEST MESSAGE:\n${redactSecrets(sourceComment.body).slice(-450)}`;
}

function extractOutputText(payload) {
  const parts = [];
  for (const item of payload?.output ?? []) {
    if (item?.type !== "message") continue;
    for (const content of item.content ?? []) {
      if (content?.type === "output_text" && content.text) parts.push(content.text);
    }
  }
  return parts.join("\n").trim();
}

async function askGrok(prompt) {
  const { xaiApiKey } = config();
  if (!xaiApiKey) throw new Error("XAI_API_KEY is not configured.");

  const response = await fetch("https://api.x.ai/v1/responses", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${xaiApiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: XAI_MODEL,
      input: prompt,
      max_output_tokens: MAX_OUTPUT_TOKENS,
      store: false,
    }),
  });

  const payload = await response.json().catch(() => ({ error: "Invalid JSON response" }));
  if (!response.ok) {
    throw new Error(`xAI API ${response.status}: ${JSON.stringify(payload)}`);
  }

  const text = extractOutputText(payload);
  if (!text) throw new Error(`xAI response had no output_text (status=${payload?.status ?? "unknown"}).`);
  return {
    text,
    responseId: payload.id ?? "unknown",
    usage: payload.usage ?? null,
  };
}

async function postGrokReply(issueNumber, sourceCommentId, grok) {
  const marker = `<!-- ai-board:provider=grok ${sourceMarker(sourceCommentId)} model=${XAI_MODEL} response-id=${grok.responseId} -->`;
  const body = `### Grok\n\n${grok.text}\n\n${marker}`;
  return githubApi(`/repos/${OWNER}/${REPO}/issues/${issueNumber}/comments`, {
    method: "POST",
    body: { body },
  });
}

function issueNumberFromComment(comment) {
  const match = String(comment.issue_url ?? "").match(/\/issues\/(\d+)$/);
  return match ? Number(match[1]) : null;
}

async function processSourceComment(comment) {
  if (!comment?.id || !String(comment.body ?? "").trim()) return;
  if (isGrokRelayComment(comment.body) || isNoRelayComment(comment.body)) return;
  if (inFlight.has(comment.id)) return;

  const issueNumber = issueNumberFromComment(comment);
  if (!issueNumber) return;

  inFlight.add(comment.id);
  try {
    const issue = await fetchIssue(issueNumber);
    if (issue.pull_request) return;

    const comments = await fetchIssueComments(issueNumber);
    if (alreadyAnswered(comments, comment.id)) return;

    const prompt = compactContext(issue, comments, comment);
    const grok = await askGrok(prompt);
    await postGrokReply(issueNumber, comment.id, grok);

    const costTicks = grok.usage?.cost_in_usd_ticks;
    const cost = Number.isFinite(Number(costTicks)) ? Number(costTicks) / 1e10 : null;
    console.log(
      JSON.stringify({
        event: "grok_reply_posted",
        issue: issueNumber,
        source_comment_id: comment.id,
        response_id: grok.responseId,
        model: XAI_MODEL,
        cost_usd: cost,
      })
    );
  } finally {
    inFlight.delete(comment.id);
  }
}

async function pollOnce() {
  if (polling) return;
  const status = configurationStatus();
  if (!status.ready) return;

  polling = true;
  const pollStarted = new Date();
  try {
    const comments = await listRecentRepoComments(sinceIso);
    for (const comment of comments) {
      try {
        await processSourceComment(comment);
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "source_comment_failed",
            comment_id: comment?.id ?? null,
            error: error instanceof Error ? error.message : String(error),
          })
        );
      }
    }
    sinceIso = new Date(pollStarted.getTime() - OVERLAP_SECONDS * 1000).toISOString();
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "poll_failed",
        error: error instanceof Error ? error.message : String(error),
      })
    );
  } finally {
    polling = false;
  }
}

const httpServer = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
    const status = configurationStatus();
    res.writeHead(status.ready ? 200 : 503, { "content-type": "application/json" });
    return res.end(
      JSON.stringify({
        ok: status.ready,
        service: "AI Board Grok Relay",
        ...status,
        since: sinceIso,
      })
    );
  }

  if (req.method === "POST" && url.pathname === "/run-once") {
    const { adminSecret } = config();
    const supplied = req.headers.authorization?.replace(/^Bearer\s+/i, "") ?? "";
    if (!adminSecret || supplied !== adminSecret) {
      res.writeHead(401).end("Unauthorized");
      return;
    }
    await pollOnce();
    res.writeHead(202, { "content-type": "application/json" }).end(JSON.stringify({ accepted: true }));
    return;
  }

  res.writeHead(404).end("Not Found");
});

httpServer.listen(PORT, "0.0.0.0", () => {
  console.log(
    JSON.stringify({
      event: "relay_started",
      port: PORT,
      ...configurationStatus(),
    })
  );
});

setInterval(() => void pollOnce(), POLL_SECONDS * 1000).unref();
setTimeout(() => void pollOnce(), 1000).unref();
