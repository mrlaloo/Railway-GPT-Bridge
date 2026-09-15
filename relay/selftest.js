import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const source = readFileSync(new URL("./server.js", import.meta.url), "utf8");

assert.match(source, /https:\/\/api\.x\.ai\/v1\/responses/);
assert.match(source, /source-comment-id=/);
assert.match(source, /ai-board:provider=grok/);
assert.match(source, /GITHUB_TOKEN/);
assert.match(source, /XAI_API_KEY/);
assert.match(source, /store:\s*false/);
assert.match(source, /isNoRelayComment/);

console.log("relay selftest: ok");
