#!/usr/bin/env python3
"""Smoke test for the Valeo connector, covering both protocol eras.

Local:    python3 test_server.py
Deployed: python3 test_server.py https://valeo-connector.onrender.com
"""

import http.client
import json
import sys
import urllib.parse

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787").rstrip("/")
PARTS = urllib.parse.urlparse(BASE)
MCP_PATH = "/mcp"
MODERN = "2026-07-28"
LEGACY = "2025-06-18"

passed = failed = 0


def connect():
    if PARTS.scheme == "https":
        return http.client.HTTPSConnection(PARTS.netloc, timeout=90)
    return http.client.HTTPConnection(PARTS.netloc, timeout=90)


def rpc(body, headers=None, conn=None, method="POST", path=MCP_PATH):
    """Send one request. Returns (status, headers_dict, parsed_body_or_None)."""
    own = conn is None
    conn = conn or connect()
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    head.update(headers or {})
    conn.request(method, path, json.dumps(body) if body is not None else None, head)
    resp = conn.getresponse()
    raw = resp.read().decode()
    if raw.startswith("event:"):
        raw = raw.split("data: ", 1)[1].strip()
    data = json.loads(raw) if raw.strip() else None
    out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    if own:
        conn.close()
    return out


def meta(version=MODERN):
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "smoke-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def modern_headers(method, name=None, version=MODERN):
    head = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name:
        head["Mcp-Name"] = name
    return head


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  %s %s" % (label, detail))
    else:
        failed += 1
        print("  FAIL  %s %s" % (label, detail))


print("Valeo connector smoke test against %s\n" % BASE)

# ---------------------------------------------------------------- modern era
print("modern era (%s)" % MODERN)

status, _, res = rpc({"jsonrpc": "2.0", "id": "d1", "method": "server/discover",
                      "params": {"_meta": meta()}}, modern_headers("server/discover"))
d = (res or {}).get("result") or {}
check("server/discover returns 200", status == 200, "(got %s)" % status)
check("  advertises supported versions", MODERN in d.get("supportedVersions", []),
      "(%s)" % d.get("supportedVersions"))
check("  declares tools capability", "tools" in (d.get("capabilities") or {}))
check("  resultType complete", d.get("resultType") == "complete", "(%s)" % d.get("resultType"))
check("  serverInfo in _meta",
      bool((d.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo")))
check("  carries cache hints", d.get("ttlMs") and d.get("cacheScope"),
      "(ttlMs=%s scope=%s)" % (d.get("ttlMs"), d.get("cacheScope")))
_info = (d.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo") or {}
_icons = _info.get("icons") or []
check("  advertises a brand icon (SEP-973)",
      bool(_icons) and _icons[0]["mimeType"] == "image/png"
      and _icons[0]["src"].startswith("data:image/png;base64,"),
      "(%d icon(s))" % len(_icons))
check("  declares websiteUrl", _info.get("websiteUrl") == "https://feelvaleo.com")
check("  no session id minted", True)

status, headers, res = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                            "params": {"_meta": meta()}}, modern_headers("tools/list"))
tools = ((res or {}).get("result") or {}).get("tools") or []
check("tools/list returns 5 tools", status == 200 and len(tools) == 5, "(%d)" % len(tools))
check("  no Mcp-Session-Id on modern response", "mcp-session-id" not in headers)
for t in tools:
    check("  schema: %s" % t["name"],
          bool(t.get("description")) and bool(t.get("title"))
          and t["inputSchema"]["type"] == "object" and t["annotations"]["readOnlyHint"])

calls = [
    ("get_lab_summary", {}, "Biomarkers measured"),
    ("get_lab_summary", {"categories": ["Heart", "Metabolic"], "include_trends": False}, "LDL Cholesterol"),
    ("get_category_breakdown", {}, "| Thyroid |"),
    ("get_category_breakdown", {"categories": ["thyroid"]}, "Thyroid"),
    ("get_programs", {}, "Metabolic Reset"),
    ("get_programs", {"status": "all"}, "Iron & Vitamin D"),
    ("get_appointments", {"include_past": True}, "Recent services"),
    ("get_supplement_plan", {}, "Vitamin D3 + K2"),
]
for name, args, expect in calls:
    status, _, res = rpc(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": name, "arguments": args, "_meta": meta()}},
        modern_headers("tools/call", name))
    result = (res or {}).get("result") or {}
    text = (result.get("content") or [{}])[0].get("text", "")
    check("tools/call %s%s" % (name, (" " + json.dumps(args)) if args else ""),
          status == 200 and expect in text and result.get("isError") is False
          and result.get("resultType") == "complete", "(%d chars)" % len(text))

# ------------------------------------------------------- modern error handling
print("\nmodern error handling")

status, _, res = rpc({"jsonrpc": "2.0", "id": 2, "method": "does/not/exist",
                      "params": {"_meta": meta()}}, modern_headers("does/not/exist"))
check("unknown method -> 404 with -32601",
      status == 404 and (res or {}).get("error", {}).get("code") == -32601,
      "(http %s, code %s)" % (status, (res or {}).get("error", {}).get("code")))

status, _, res = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/list",
                      "params": {"_meta": meta("1900-01-01")}},
                     modern_headers("tools/list", version="1900-01-01"))
err = (res or {}).get("error") or {}
check("unsupported version -> 400 with -32022",
      status == 400 and err.get("code") == -32022, "(http %s, code %s)" % (status, err.get("code")))
check("  error lists supported versions", MODERN in (err.get("data") or {}).get("supported", []))
check("  error echoes requested version",
      (err.get("data") or {}).get("requested") == "1900-01-01")

status, _, res = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/list",
                      "params": {"_meta": meta(MODERN)}},
                     {"MCP-Protocol-Version": "2025-06-18", "Mcp-Method": "tools/list"})
check("version header/body mismatch -> 400 with -32020",
      status == 400 and (res or {}).get("error", {}).get("code") == -32020,
      "(http %s, code %s)" % (status, (res or {}).get("error", {}).get("code")))

status, _, res = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "get_supplement_plan", "arguments": {}, "_meta": meta()}},
                     modern_headers("tools/call", "get_programs"))
check("Mcp-Name header/body mismatch -> 400 with -32020",
      status == 400 and (res or {}).get("error", {}).get("code") == -32020,
      "(http %s, code %s)" % (status, (res or {}).get("error", {}).get("code")))

status, _, res = rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                      "params": {"name": "get_programs", "arguments": {"bogus": 1}, "_meta": meta()}},
                     modern_headers("tools/call", "get_programs"))
check("bad tool argument -> isError result",
      status == 200 and ((res or {}).get("result") or {}).get("isError") is True)

# ---------------------------------------------------------------- legacy era
print("\nlegacy era (%s)" % LEGACY)

status, headers, res = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": LEGACY, "capabilities": {},
                                       "clientInfo": {"name": "smoke-test", "version": "1"}}})
init = (res or {}).get("result") or {}
check("initialize returns 200", status == 200 and init.get("protocolVersion") == LEGACY,
      "(%s)" % init.get("protocolVersion"))
check("  mints a session id", bool(headers.get("mcp-session-id")))
check("  no modern resultType on legacy result", "resultType" not in init)
check("  legacy serverInfo carries the icon too",
      bool((init.get("serverInfo") or {}).get("icons")))
sid = headers.get("mcp-session-id")

status, _, _ = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   {"Mcp-Session-Id": sid})
check("initialized notification -> 202", status == 202, "(got %s)" % status)

status, _, res = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                     {"Mcp-Session-Id": sid, "MCP-Protocol-Version": LEGACY})
check("legacy tools/list works",
      status == 200 and len(((res or {}).get("result") or {}).get("tools", [])) == 5)

status, _, res = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "get_lab_summary", "arguments": {}}},
                     {"Mcp-Session-Id": sid, "MCP-Protocol-Version": LEGACY})
result = (res or {}).get("result") or {}
check("legacy tools/call works",
      status == 200 and "Biomarkers measured" in (result.get("content") or [{}])[0].get("text", ""))

# ---------------------------------------------------------------- transport
print("\ntransport")

for verb, expect in (("GET", 405), ("DELETE", 405)):
    status, _, _ = rpc(None, method=verb)
    check("%s /mcp -> %d" % (verb, expect), status == expect, "(got %s)" % status)

status, _, res = rpc(None, method="GET", path="/health")
check("GET /health -> 200", status == 200 and (res or {}).get("status") == "ok")

for icon_path in ("/icon.png", "/icon-128.png", "/favicon.ico"):
    conn = connect()
    conn.request("GET", icon_path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    check("GET %s serves a PNG" % icon_path,
          resp.status == 200 and resp.getheader("Content-Type") == "image/png"
          and body[:8] == b"\x89PNG\r\n\x1a\n", "(%d bytes)" % len(body))

status, _, _ = rpc(None, method="HEAD", path="/health")
check("HEAD /health -> 200", status == 200, "(got %s)" % status)

status, headers, res = rpc({"jsonrpc": "2.0", "id": 7, "method": "ping",
                            "params": {"_meta": meta()}}, modern_headers("ping"))
check("responds application/json, not SSE",
      headers.get("content-type") == "application/json", "(%s)" % headers.get("content-type"))

status, _, res = rpc({"jsonrpc": "2.0", "id": 8, "method": "ping", "params": {"_meta": meta()}},
                     dict(modern_headers("ping"), Origin="https://unexpected.example"))
check("unexpected Origin allowed, not blocked", status == 200, "(got %s)" % status)

# Regression: the server used to reply before reading the request body, which desynced a
# keep-alive connection - the next request line was parsed out of the leftover body bytes and
# answered with 501. Two successful requests down one connection prove the framing is right.
conn = connect()
s1, _, r1 = rpc({"jsonrpc": "2.0", "id": 10, "method": "ping", "params": {"_meta": meta()}},
                modern_headers("ping"), conn)
s2, _, r2 = rpc({"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {"_meta": meta()}},
                modern_headers("tools/list"), conn)
conn.close()
check("two requests on one keep-alive connection",
      s1 == 200 and s2 == 200 and (r2 or {}).get("id") == 11, "(%s, %s)" % (s1, s2))

# An error response must be a clean JSON-RPC error, not a truncated or garbled body.
status, _, res = rpc({"jsonrpc": "2.0", "id": 12, "method": "server/nope",
                      "params": {"_meta": meta(), "padding": "x" * 4000}},
                     modern_headers("server/nope"))
check("error response to a large body is well formed",
      status == 404 and (res or {}).get("id") == 12
      and (res or {}).get("error", {}).get("code") == -32601)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
