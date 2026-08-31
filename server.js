import { createServer } from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { parse } from "graphql";
import { z } from "zod";

const RAILWAY_API = "https://backboard.railway.com/graphql/v2";
const port = Number(process.env.PORT ?? 8787);
const pathSecret = process.env.MCP_PATH_SECRET?.trim();
const mcpPath = pathSecret ? `/mcp-${pathSecret}` : "/mcp-disabled";

const blockedWords = /\b(token|secret|environmentVariables|variableCollection|variables)\b/i;

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
  return payload;
}

function textResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function createRailwayServer() {
  const server = new McpServer({ name: "railway-gpt-bridge", version: "1.0.0" });

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

  return server;
}

const httpServer = createServer(async (req, res) => {
  if (!req.url) return res.writeHead(400).end("Missing URL");
  const url = new URL(req.url, `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "GET" && url.pathname === "/") {
    return res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true, service: "Railway GPT Bridge", mode: "read-only" }));
  }

  if (!pathSecret || pathSecret.length < 24) {
    return res.writeHead(503).end("MCP_PATH_SECRET must be at least 24 characters.");
  }

  if (req.method === "OPTIONS" && url.pathname === mcpPath) {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "content-type, mcp-session-id",
      "Access-Control-Expose-Headers": "Mcp-Session-Id",
    });
    return res.end();
  }

  if (url.pathname === mcpPath && new Set(["POST", "GET", "DELETE"]).has(req.method ?? "")) {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
    const server = createRailwayServer();
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.on("close", () => { transport.close(); server.close(); });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res);
    } catch (error) {
      console.error("MCP request failed", error);
      if (!res.headersSent) res.writeHead(500).end("Internal server error");
    }
    return;
  }

  res.writeHead(404).end("Not Found");
});

httpServer.listen(port, "0.0.0.0", () => {
  console.log(`Railway GPT Bridge listening on port ${port} in read-only mode`);
});
