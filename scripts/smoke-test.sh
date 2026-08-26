#!/usr/bin/env bash
# Verify the bearer gateway in front of the Basic Memory MCP server.
#
#   BASE_URL=http://localhost:8080 MCP_TOKEN=... ./scripts/smoke-test.sh
#
# Checks, in order:
#   1. no token            -> 401 + WWW-Authenticate
#   2. wrong token         -> 401
#   3. valid token         -> initialize succeeds, server identifies itself
#   4. valid token         -> tools/list returns the Basic Memory tools
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
MCP_URL="${BASE_URL%/}/mcp"
: "${MCP_TOKEN:?MCP_TOKEN is required}"

for cmd in curl jq; do
	command -v "$cmd" >/dev/null || { echo "missing dependency: $cmd" >&2; exit 1; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }

# The streamable-http transport answers with an SSE stream; pull the JSON out.
sse_payload() { sed -n 's/^data: //p' "$1" | head -1; }

mcp_post() {
	# mcp_post <header-dump> <body> [extra curl args...]
	local dump="$1" body="$2"; shift 2
	curl -sS -o "$work/body" -D "$dump" -w '%{http_code}' \
		-X POST "$MCP_URL" \
		-H 'Content-Type: application/json' \
		-H 'Accept: application/json, text/event-stream' \
		"$@" -d "$body"
}

INIT_BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}'

echo "Testing $MCP_URL"

# 1. No credentials at all.
code="$(mcp_post "$work/h1" "$INIT_BODY")"
[ "$code" = "401" ] || fail "unauthenticated request returned $code, expected 401"
grep -qi '^www-authenticate:' "$work/h1" || fail "401 response is missing the WWW-Authenticate header"
pass "unauthenticated request rejected with 401"

# 2. Wrong credentials.
code="$(mcp_post "$work/h2" "$INIT_BODY" -H "Authorization: Bearer not-the-token")"
[ "$code" = "401" ] || fail "bad token returned $code, expected 401"
pass "invalid token rejected with 401"

# 3. Valid credentials: initialize.
code="$(mcp_post "$work/h3" "$INIT_BODY" -H "Authorization: Bearer $MCP_TOKEN")"
[ "$code" = "200" ] || fail "initialize returned $code, expected 200 (body: $(head -c 300 "$work/body"))"
init="$(sse_payload "$work/body")"
[ -n "$init" ] || init="$(cat "$work/body")"
name="$(printf '%s' "$init" | jq -r '.result.serverInfo.name // empty')"
[ -n "$name" ] || fail "initialize response has no serverInfo.name: $(head -c 300 <<<"$init")"
pass "initialize succeeded (server: $name)"

session="$(grep -i '^mcp-session-id:' "$work/h3" | tr -d '\r' | cut -d' ' -f2- || true)"
[ -n "$session" ] || fail "no Mcp-Session-Id header returned by initialize"

# The spec requires this notification before any other request.
mcp_post "$work/h4" '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
	-H "Authorization: Bearer $MCP_TOKEN" -H "Mcp-Session-Id: $session" >/dev/null

# 4. Valid credentials: tools/list.
code="$(mcp_post "$work/h5" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
	-H "Authorization: Bearer $MCP_TOKEN" -H "Mcp-Session-Id: $session")"
[ "$code" = "200" ] || fail "tools/list returned $code, expected 200 (body: $(head -c 300 "$work/body"))"
tools="$(sse_payload "$work/body")"
[ -n "$tools" ] || tools="$(cat "$work/body")"
count="$(printf '%s' "$tools" | jq -r '.result.tools | length // 0')"
[ "${count:-0}" -gt 0 ] || fail "tools/list returned no tools: $(head -c 300 <<<"$tools")"
printf '%s' "$tools" | jq -e '.result.tools[] | select(.name == "write_note")' >/dev/null \
	|| fail "tools/list does not include write_note"
pass "tools/list returned $count tools, including write_note"

echo "All checks passed."
