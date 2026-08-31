# Railway GPT Bridge

Private, read-only MCP bridge between ChatGPT and the Railway GraphQL API.

## Railway variables

- `RAILWAY_TOKEN`: Railway workspace API token.
- `MCP_PATH_SECRET`: A random value of at least 24 characters. The MCP endpoint becomes `https://YOUR-DOMAIN/mcp-YOUR_SECRET`.

The first version permits GraphQL queries only. Mutations and fields related to tokens, secrets, or environment variables are blocked.

## Deploy

Deploy this repository as a Railway service, set both variables, and generate a public Railway domain. Use the complete secret MCP endpoint when creating a private ChatGPT developer-mode plugin.
