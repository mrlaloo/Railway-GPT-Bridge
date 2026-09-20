import { createServer } from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { parse } from "graphql";
import { z } from "zod";

const RAILWAY_API = "https://backboard.railway.com/graphql/v2";
const port = Number(process.env.PORT ?? 8787);

const pathSecret = process.env.MCP_PATH_SECRET?.trim();
const mcpPath = pathSecret ? `/mcp-${pathSecret}` : "/mcp-disabled";

const grokPathSecret = process.env.GROK_MCP_PATH_SECRET?.trim();
const grokMcpPath = grokPathSecret ? `/grok-mcp-${grokPathSecret}` : "/grok-mcp-disabled";

const blockedWords = /\b(token|secret|environmentVariables|variableCollection|variables)\b/i;

const ALLOWED_SERVICES = Object.freeze({
  alfred: Object.freeze({
    key: "alfred",
    name: "Alfred Crypto",
    projectId: "5c903f86-8186-4c59-a1a3-03ef5cfe92c1",
    environmentId: "9ed78d4b-d19b-417b-a061-91ee8ba42324",
    serviceId: "198922a0-c60c-408c-8b41-22132dfbdccf",
  }),
  moneypenny: Object.freeze({
    key: "moneypenny",
    name: "MoneyPenny-Forex",
    projectId: "86ccfe6c-ff45-457c-a8ff-6a791d3f3d30",
    environmentId: "dfc01115-1d8d-41df-ac15-81f133ad04fe",
    serviceId: "df7fe3e3-f73e-49a2-96ff-0bc738e8c502",
  }),
});

function railwayToken() {
  const token = process.env.RAILWAY_TOKEN?.trim();
  if (!token) throw new Error("RAILWAY_TOKEN is not configured.");
  return token;
}

function assertReadOnly(query) {
  if (blockedWords.test(query)) {
    throw new Error("Queries for tokens, secrets, or environment variables are blocked.");
  }
  const document = parse(query);
  for (const definition of document.definitions) {
    if (definition.kind === "OperationDefinition" && definition.operation !== "query") {
      throw new Error("Only read-only GraphQL queries are allowed.");
    }
  }
}

async function railwayGraphql(query, variables = {}) {
  assertReadOnly(query);
  const response = await fetch(RAILWAY_API, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${railwayToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });
  const payload = await response.json().catch(() => ({ error: "Invalid JSON response" }));
  if (!response.ok) throw new Error(`Railway API ${response.status}: ${JSON.stringify(payload)}`);
  if (payload.errors?.length) throw new Error(`Railway GraphQL: ${JSON.stringify(payload.errors)}`);
  return payload;
}

function textResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function allowedService(key) {
  const svc = ALLOWED_SERVICES[key];
  if (!svc) throw new Error("Service is not in the Grok read-only allowlist.");
  return svc;
}

async function recentDeploymentsFor(service, limit = 10) {
  const result = await railwayGraphql(
    `query deployments($input: DeploymentListInput!, $first: Int) {
      deployments(input: $input, first: $first) {
        edges { node { id status createdAt url staticUrl meta } }
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
  return result.data?.deployments?.edges?.map((edge) => edge.node) ?? [];
}

async function latestSuccessfulDeployment(service) {
  const result = await railwayGraphql(
    `query latestDeployment($input: DeploymentListInput!) {
      deployments(input: $input, first: 1) {
        edges { node { id status createdAt url meta } }
      }
    }`,
    {
      input: {
        projectId: service.projectId,
        serviceId: service.serviceId,
        environmentId: service.environmentId,
        status: { successfulOnly: true },
      },
    }
  );
  return result.data?.deployments?.edges?.[0]?.node ?? null;
}

function registerRestrictedGrokTools(server) {
  const serviceEnum = z.enum(["alfred", "moneypenny"]);

  server.registerTool(
    "trading_bot_services",
    {
      title: "List allowed trading bot services",
      description: "Lists the only Railway services available through this connector: Alfred Crypto and MoneyPenny. Read-only.",
      inputSchema: {},
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    },
    async () => textResult({
      mode: "read-only",
      services: Object.values(ALLOWED_SERVICES).map(({ key, name }) => ({ key, name })),
      prohibited: ["deploy", "redeploy", "restart", "variables", "secrets", "delete", "trading actions"],
    })
  );

  server.registerTool(
    "trading_bot_status",
    {
      title: "Get trading bot Railway status",
      description: "Returns the latest successful Railway deployment metadata for Alfred Crypto or MoneyPenny. Read-only.",
      inputSchema: { service: serviceEnum },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ service }) => {
      const svc = allowedService(service);
      const deployment = await latestSuccessfulDeployment(svc);
      return textResult({ service: svc.name, deployment });
    }
  );

  server.registerTool(
    "trading_bot_recent_deployments",
    {
      title: "List recent trading bot deployments",
      description: "Lists recent Railway deployments for Alfred Crypto or MoneyPenny only. Read-only.",
      inputSchema: {
        service: serviceEnum,
        limit: z.number().int().min(1).max(20).optional(),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ service, limit }) => {
      const svc = allowedService(service);
      const deployments = await recentDeploymentsFor(svc, limit ?? 10);
      return textResult({ service: svc.name, deployments });
    }
  );

  server.registerTool(
    "trading_bot_logs",
    {
      title: "Read trading bot Railway logs",
      description: "Reads runtime or build logs for Alfred Crypto or MoneyPenny only. If deployment_id is omitted, uses the latest successful deployment. This tool cannot deploy, restart, change variables, or place trades.",
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
      const svc = allowedService(service);
      let deploymentId = deployment_id;
      if (!deploymentId) {
        const latest = await latestSuccessfulDeployment(svc);
        if (!latest?.id) throw new Error(`No successful deployment found for ${svc.name}.`);
        deploymentId = latest.id;
      } else {
        const recent = await recentDeploymentsFor(svc, 20);
        if (!recent.some((row) => row.id === deploymentId)) {
          throw new Error("deployment_id is not a recent deployment of the selected allowed service.");
        }
      }

      const logLimit = limit ?? 200;
      const logKind = kind ?? "runtime";
      let result;
      if (logKind === "build") {
        result = await railwayGraphql(
          `query buildLogs($deploymentId: String!, $limit: Int) {
            buildLogs(deploymentId: $deploymentId, limit: $limit) { timestamp message severity }
          }`,
          { deploymentId, limit: logLimit }
        );
        return textResult({
          service: svc.name,
          deploymentId,
          kind: "build",
          logs: result.data?.buildLogs ?? [],
        });
      }

      result = await railwayGraphql(
        `query deploymentLogs($deploymentId: String!, $limit: Int, $filter: String, $startDate: DateTime, $endDate: DateTime) {
          deploymentLogs(deploymentId: $deploymentId, limit: $limit, filter: $filter, startDate: $startDate, endDate: $endDate) {
            timestamp message severity
          }
        }`,
        {
          deploymentId,
          limit: logLimit,
          filter: filter || null,
          startDate: start_date || null,
          endDate: end_date || null,
        }
      );
      return textResult({
        service: svc.name,
        deploymentId,
        kind: "runtime",
        filter: filter || null,
        logs: result.data?.deploymentLogs ?? [],
      });
    }
  );
}

function registerGenericReadOnlyTools(server) {
  server.registerTool(
    "railway_schema_search",
    {
      title: "Search Railway API schema",
      description: "Searches Railway's live GraphQL schema for read-only fields and types before composing a precise query.",
      inputSchema: { term: z.string().min(2).max(80) },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ term }) => {
      const result = await railwayGraphql(`query SchemaSearch {
        __schema {
          queryType { fields { name description type { kind name ofType { kind name } } } }
          types { name kind description fields { name description type { kind name ofType { kind name } } } }
        }
      }`);
      const needle = term.toLowerCase();
      const schema = result.data?.__schema;
      const matches = [];
      for (const field of schema?.queryType?.fields ?? []) {
        if (`${field.name} ${field.description ?? ""}`.toLowerCase().includes(needle)) matches.push({ scope: "Query", ...field });
      }
      for (const type of schema?.types ?? []) {
        const typeMatches = `${type.name ?? ""} ${type.description ?? ""}`.toLowerCase().includes(needle);
        const fields = (type.fields ?? []).filter((field) => `${field.name} ${field.description ?? ""}`.toLowerCase().includes(needle));
        if (typeMatches || fields.length) matches.push({ scope: "Type", name: type.name, kind: type.kind, fields });
        if (matches.length >= 40) break;
      }
      return textResult({ term, matches: matches.slice(0, 40) });
    }
  );

  server.registerTool(
    "railway_read_query",
    {
      title: "Read Railway data",
      description: "Runs a read-only Railway GraphQL query for projects, services, deployments, status, usage, and logs. Mutations and secret-variable fields are blocked.",
      inputSchema: {
        query: z.string().min(8).max(12000),
        variables: z.record(z.string(), z.unknown()).optional(),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    },
    async ({ query, variables }) => textResult(await railwayGraphql(query, variables ?? {}))
  );
}

function createRailwayServer(mode = "generic") {
  const server = new McpServer({
    name: mode === "grok" ? "railway-trading-bots-readonly" : "railway-gpt-bridge",
    version: "1.1.0",
  });
  if (mode === "grok") registerRestrictedGrokTools(server);
  else registerGenericReadOnlyTools(server);
  return server;
}

function setCors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
}

async function handleMcp(req, res, mode) {
  setCors(res);
  const server = createRailwayServer(mode);
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  res.on("close", () => { transport.close(); server.close(); });
  try {
    await server.connect(transport);
    await transport.handleRequest(req, res);
  } catch (error) {
    console.error(`MCP ${mode} request failed`, error);
    if (!res.headersSent) res.writeHead(500).end("Internal server error");
  }
}

const httpServer = createServer(async (req, res) => {
  if (!req.url) return res.writeHead(400).end("Missing URL");
  const url = new URL(req.url, `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && url.pathname === "/") {
    return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
      ok: true,
      service: "Railway GPT Bridge",
      genericMode: "read-only",
      grokMode: grokPathSecret ? "trading-bots-read-only" : "disabled",
    }));
  }

  const isExistingPath = pathSecret && pathSecret.length >= 24 && url.pathname === mcpPath;
  const isGrokPath = grokPathSecret && grokPathSecret.length >= 24 && url.pathname === grokMcpPath;

  if (req.method === "OPTIONS" && (isExistingPath || isGrokPath)) {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "content-type, mcp-session-id",
      "Access-Control-Expose-Headers": "Mcp-Session-Id",
    });
    return res.end();
  }

  if ((isExistingPath || isGrokPath) && new Set(["POST", "GET", "DELETE"]).has(req.method ?? "")) {
    return handleMcp(req, res, isGrokPath ? "grok" : "generic");
  }

  res.writeHead(404).end("Not Found");
});

httpServer.listen(port, "0.0.0.0", () => {
  console.log(`Railway GPT Bridge listening on port ${port}; Grok trading-bot log access=${grokPathSecret ? "enabled" : "disabled"}`);
});
