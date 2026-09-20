import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const RAILWAY_API = "https://backboard.railway.com/graphql/v2";
const port = Number(process.env.PORT ?? 8080);
const pathSecret = process.env.GROK_MCP_PATH_SECRET?.trim() ?? "";
const mcpPath = pathSecret ? `/mcp/${pathSecret}` : "/mcp-disabled";

const SERVICES = Object.freeze({
  alfred: Object.freeze({
    key: "alfred",
    name: "Alfred Crypto",
    projectId: "5c903f86-8186-4c59-a1a3-03ef5cfe92c1",
    environmentId: "9ed78d4b-d19b-417b-a061-91ee8ba42324",
    serviceId: "198922a0-c60c-408c-8b41-22132dfbdccf",
    tokenEnv: "ALFRED_RAILWAY_PROJECT_TOKEN",
  }),
  darkvenus: Object.freeze({
    key: "darkvenus",
    name: "Dark Venus Stocks",
    projectId: "5c903f86-8186-4c59-a1a3-03ef5cfe92c1",
    environmentId: "9ed78d4b-d19b-417b-a061-91ee8ba42324",
    serviceId: "59ff9177-8215-4828-bc01-18f196074c53",
    tokenEnv: "ALFRED_RAILWAY_PROJECT_TOKEN",
  }),
  moneypenny: Object.freeze({
    key: "moneypenny",
    name: "MoneyPenny-Forex",
    projectId: "86ccfe6c-ff45-457c-a8ff-6a791d3f3d30",
    environmentId: "dfc01115-1d8d-41df-ac15-81f133ad04fe",
    serviceId: "df7fe3e3-f73e-49a2-96ff-0bc738e8c502",
    tokenEnv: "MONEYPENNY_RAILWAY_PROJECT_TOKEN",
  }),
});

function serviceFor(key) {
  const svc = SERVICES[key];
  if (!svc) throw new Error("Service is not in the Grok allowlist.");
  return svc;
}

function projectTokenFor(service) {
  const token = process.env[service.tokenEnv]?.trim();
  if (!token) throw new Error(`${service.tokenEnv} is not configured.`);
  return token;
}

function workspaceTokenFor() {
  return process.env.RAILWAY_WORKSPACE_TOKEN?.trim() || null;
}

async function railwayQuery(service, query, variables = {}) {
  if (/\bmutation\b/i.test(query)) throw new Error("Mutations are disabled.");
  if (/\b(variable|secret|token)\b/i.test(query)) throw new Error("Secret/variable queries are disabled.");

  const workspaceToken = workspaceTokenFor();
  const headers = workspaceToken
    ? {
        Authorization: `Bearer ${workspaceToken}`,
        "Content-Type": "application/json",
      }
    : {
        "Project-Access-Token": projectTokenFor(service),
        "Content-Type": "application/json",
      };

  const response = await fetch(RAILWAY_API, {
    method: "POST",
    headers,
    body: JSON.stringify({ query, variables }),
  });

  const payload = await response.json().catch(() => ({ error: "Invalid JSON response" }));
  if (!response.ok) throw new Error(`Railway API ${response.status}: ${JSON.stringify(payload)}`);
  if (payload.errors?.length) throw new Error(`Railway GraphQL: ${JSON.stringify(payload.errors)}`);
  return payload;
}

async function recentDeployments(service, limit = 10) {
  const payload = await railwayQuery(
    service,
    `query deployments($input: DeploymentListInput!, $first: Int) {
      deployments(input: $input, first: $first) {
        edges { node { id status createdAt meta } }
      }
    }`,
    {
      input: {
        projectId: service.projectId,
        serviceId: service.serviceId,
        environmentId: service.environmentId,
      },
      first: limit,
    }
  );
  return payload.data?.deployments?.edges?.map((edge) => edge.node) ?? [];
}

async function latestSuccessful(service) {
  const payload = await railwayQuery(
    service,
    `query latestDeployment($input: DeploymentListInput!, $first: Int) {
      deployments(input: $input, first: $first) {
        edges { node { id status createdAt meta } }
      }
    }`,
    {
      input: {
        projectId: service.projectId,
        serviceId: service.serviceId,
        environmentId: service.environmentId,
      },
      first: 5,
    }
  );
  const nodes = payload.data?.deployments?.edges?.map((edge) => edge.node) ?? [];
  return nodes.find((node) => node.status === "SUCCESS") ?? null;
}

async function readLogs(service, deploymentId, kind, limit, filter, startDate, endDate) {
  if (kind === "build") {
    const payload = await railwayQuery(
      service,
      `query buildLogs($deploymentId: String!, $limit: Int) {
        buildLogs(deploymentId: $deploymentId, limit: $limit) { timestamp message severity }
      }`,
      { deploymentId, limit }
    );
    return payload.data?.buildLogs ?? [];
  }

  const payload = await railwayQuery(
    service,
    `query deploymentLogs($deploymentId: String!, $limit: Int, $filter: String, $startDate: DateTime, $endDate: DateTime) {
      deploymentLogs(deploymentId: $deploymentId, limit: $limit, filter: $filter, startDate: $startDate, endDate: $endDate) {
        timestamp message severity
      }
    }`,
    {
      deploymentId,
      limit,
      filter: filter || null,
      startDate: startDate || null,
      endDate: endDate || null,
    }
  );
  return payload.data?.deploymentLogs ?? [];
}

function text(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function createMcpServer() {
  const server = new McpServer({ name: "grok-trading-bot-railway-readonly", version: "1.0.0" });
  const serviceEnum = z.enum(["alfred", "moneypenny", "darkvenus"]);

  server.registerTool(
    "trading_bot_services",
    {
      title: "List trading bot services",
      description: "Lists the Railway services exposed to Grok: Alfred Crypto, MoneyPenny, and Dark Venus Stocks. Read-only.",
      inputSchema: {},
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    },
    async () => text({
      mode: "read-only",
      services: Object.values(SERVICES).map(({ key, name }) => ({ key, name })),
      prohibited: ["deploy", "redeploy", "restart", "variables", "secrets", "delete", "trade"],
    })
  );

  server.registerTool(
    "trading_bot_status",
    {
      title: "Get trading bot deployment status",
      description: "Returns latest successful deployment metadata for an allowed trading bot service.",
      inputSchema: { service: serviceEnum },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ service }) => {
      const svc = serviceFor(service);
      return text({ service: svc.name, deployment: await latestSuccessful(svc) });
    }
  );

  server.registerTool(
    "trading_bot_recent_deployments",
    {
      title: "List recent bot deployments",
      description: "Lists recent Railway deployments for an allowed trading bot service only.",
      inputSchema: { service: serviceEnum, limit: z.number().int().min(1).max(20).optional() },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ service, limit }) => {
      const svc = serviceFor(service);
      return text({ service: svc.name, deployments: await recentDeployments(svc, limit ?? 10) });
    }
  );

  server.registerTool(
    "trading_bot_logs",
    {
      title: "Read trading bot Railway logs",
      description: "Reads runtime or build logs for an allowed trading bot service. Cannot deploy, restart, change variables, or place trades.",
      inputSchema: {
        service: serviceEnum,
        kind: z.enum(["runtime", "build"]).optional(),
        deployment_id: z.string().min(8).max(80).optional(),
        limit: z.number().int().min(1).max(500).optional(),
        filter: z.string().max(500).optional(),
        start_date: z.string().max(64).optional(),
        end_date: z.string().max(64).optional(),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ service, kind, deployment_id, limit, filter, start_date, end_date }) => {
      const svc = serviceFor(service);
      let deploymentId = deployment_id;
      if (!deploymentId) {
        const latest = await latestSuccessful(svc);
        if (!latest?.id) throw new Error(`No successful deployment found for ${svc.name}.`);
        deploymentId = latest.id;
      } else {
        const recent = await recentDeployments(svc, 20);
        if (!recent.some((d) => d.id === deploymentId)) {
          throw new Error("deployment_id does not belong to a recent deployment of this allowed service.");
        }
      }

      const logKind = kind ?? "runtime";
      const logs = await readLogs(svc, deploymentId, logKind, limit ?? 200, filter, start_date, end_date);
      return text({ service: svc.name, deploymentId, kind: logKind, logs });
    }
  );

  return server;
}

function authorizedPath(pathname) {
  if (!pathSecret || pathSecret.length < 32) return false;
  const expected = Buffer.from(mcpPath);
  const actual = Buffer.from(pathname);
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

async function selfTest() {
  for (const svc of Object.values(SERVICES)) {
    if (!process.env[svc.tokenEnv]?.trim()) {
      console.log(`GROK_RAILWAY_SELFTEST service=${svc.key} status=credential_missing`);
      continue;
    }
    try {
      const latest = await latestSuccessful(svc);
      const logs = latest?.id ? await readLogs(svc, latest.id, "runtime", 1) : [];
      console.log(`GROK_RAILWAY_SELFTEST service=${svc.key} status=ok deployment=${latest?.id ?? "none"} log_rows=${logs.length}`);
    } catch (error) {
      console.error(`GROK_RAILWAY_SELFTEST service=${svc.key} status=failed error=${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

const httpServer = createServer(async (req, res) => {
  if (!req.url) return res.writeHead(400).end("Missing URL");
  const url = new URL(req.url, `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && url.pathname === "/") {
    return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
      ok: true,
      service: "Grok Railway Trading Bot Log Bridge",
      mode: "read-only",
      services: ["Alfred Crypto", "MoneyPenny", "Dark Venus Stocks"],
    }));
  }

  if (req.method === "OPTIONS" && authorizedPath(url.pathname)) {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "content-type, mcp-session-id",
      "Access-Control-Expose-Headers": "Mcp-Session-Id",
    });
    return res.end();
  }

  if (authorizedPath(url.pathname) && new Set(["POST", "GET", "DELETE"]).has(req.method ?? "")) {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
    const server = createMcpServer();
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.on("close", () => { transport.close(); server.close(); });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res);
    } catch (error) {
      console.error("Grok MCP request failed", error);
      if (!res.headersSent) res.writeHead(500).end("Internal server error");
    }
    return;
  }

  res.writeHead(404).end("Not Found");
});

httpServer.listen(port, "0.0.0.0", () => {
  console.log(`Grok Railway Trading Bot Log Bridge listening on port ${port} in read-only mode`);
  void selfTest();
});
