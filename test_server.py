#!/usr/bin/env python3
"""Smoke test for the Valeo connector. Start server.py first, then: python3 test_server.py"""
import json, sys, urllib.request, urllib.error

BASE = "http://127.0.0.1:8787"
URL = BASE + "/mcp"
passed = failed = 0

def call(payload, accept="application/json, text/event-stream", method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(URL, data=data, method=method,
                                headers={"Content-Type": "application/json", "Accept": accept,
                                         "MCP-Protocol-Version": "2025-06-18"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            if body.startswith("event:"):
                body = body.split("data: ", 1)[1].strip()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, None

def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  %s %s" % (label, detail))
    else:
        failed += 1
        print("  FAIL  %s %s" % (label, detail))

print("Valeo connector smoke test\n")

status, res = call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "smoke-test", "version": "1"}}})
check("initialize", status == 200 and res["result"]["protocolVersion"] == "2025-06-18")

status, _ = call({"jsonrpc": "2.0", "method": "notifications/initialized"})
check("initialized notification -> 202", status == 202, "(got %s)" % status)

status, res = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = res["result"]["tools"] if res else []
check("tools/list", status == 200 and len(tools) == 5, "(%d tools)" % len(tools))
for t in tools:
    check("  schema: %s" % t["name"],
          bool(t.get("description")) and t["inputSchema"]["type"] == "object" and t["annotations"]["readOnlyHint"])

status, res = call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
check("ping", status == 200 and res["result"] == {})

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
    status, res = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": name, "arguments": args}})
    text = res["result"]["content"][0]["text"] if res and "result" in res else ""
    ok = status == 200 and expect in text and res["result"]["isError"] is False
    check("tools/call %s%s" % (name, (" " + json.dumps(args)) if args else ""), ok,
          "(%d chars)" % len(text))

status, res = call({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                    "params": {"name": "nope", "arguments": {}}})
check("unknown tool -> JSON-RPC error", status == 200 and "error" in res)

status, res = call({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                    "params": {"name": "get_programs", "arguments": {"bogus": 1}}})
check("bad argument -> isError result", status == 200 and res["result"]["isError"] is True)

status, res = call({"jsonrpc": "2.0", "id": 12, "method": "tools/list"}, accept="application/json")
check("plain JSON response path", status == 200 and "result" in res)

req = urllib.request.Request(URL, method="GET", headers={"Accept": "text/event-stream"})
try:
    urllib.request.urlopen(req, timeout=5); code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("GET /mcp -> 405", code == 405, "(got %s)" % code)

req = urllib.request.Request(URL, data=b"{}", method="POST",
                             headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
try:
    urllib.request.urlopen(req, timeout=5); code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("hostile Origin -> 403", code == 403, "(got %s)" % code)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
