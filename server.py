#!/usr/bin/env python3
"""
Valeo Connector - a minimal remote MCP server for Claude custom connectors.

Zero dependencies: standard library only, runs on Python 3.7+.
Transport: Streamable HTTP (MCP spec 2025-06-18) on a single /mcp endpoint.
Auth: none (demo). Add OAuth later - see README.

Run:  python3 server.py            (listens on http://127.0.0.1:8787/mcp)
      PORT=9000 HOST=0.0.0.0 python3 server.py
"""

import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sample_data as db

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
SERVER_NAME = "valeo-connector"
SERVER_VERSION = "0.1.0"
MCP_PATH = "/mcp"


# =============================================================================
# Tool implementations - each returns a markdown string for Claude to read.
# Swap the db.* lookups for real Valeo API calls to go live.
# =============================================================================

def _match(requested, available):
    """Case-insensitive filter helper. Empty/None request means 'all'."""
    if not requested:
        return available
    wanted = {c.strip().lower() for c in requested}
    return [item for item in available if item["category"].lower() in wanted]


def tool_lab_summary(categories=None, out_of_range_only=False, include_trends=True):
    cats = _match(categories, db.LAB_PANEL["categories"])
    if not cats:
        return "No matching biomarker categories found. Available: " + ", ".join(
            c["category"] for c in db.LAB_PANEL["categories"])

    total = sum(c["total"] for c in cats)
    in_range = sum(c["in_range"] for c in cats)
    out_of_range = sum(c["out_of_range"] for c in cats)
    pct = round(in_range / total * 100) if total else 0

    lines = [
        "# Lab results summary for %s" % db.MEMBER["first_name"],
        "",
        "**Panel:** %s" % db.LAB_PANEL["panel_name"],
        "**Collected:** %s (%s)" % (db.LAB_PANEL["collected_on"], db.LAB_PANEL["collection_method"]),
        "",
        "- Biomarkers measured: **%d**" % total,
        "- In optimal range: **%d** (%d%%)" % (in_range, pct),
        "- Outside optimal range: **%d**" % out_of_range,
    ]

    flagged = [(c["category"], f) for c in cats for f in c["flagged"]]
    if flagged:
        lines += ["", "## Markers needing attention"]
        for cat, f in flagged:
            lines.append("- **%s** (%s) - %s, %s" % (f["marker"], cat, f["direction"], f["severity"]))
    else:
        lines += ["", "Every marker in the selected categories is within optimal range."]

    if include_trends and not out_of_range_only and db.LAB_PANEL.get("trends"):
        lines += ["", "## Trends vs previous panel"]
        for marker, trend in db.LAB_PANEL["trends"].items():
            lines.append("- **%s**: %s" % (marker, trend))

    lines += ["", "_Summary-level data only. Discuss specific values with your Valeo clinician._"]
    return "\n".join(lines)


def tool_category_breakdown(categories=None):
    cats = _match(categories, db.LAB_PANEL["categories"])
    if not cats:
        return "No matching categories. Available: " + ", ".join(
            c["category"] for c in db.LAB_PANEL["categories"])

    lines = ["# Results by health category (%d)" % len(cats), "",
             "| Category | Measured | In range | Out of range |",
             "| --- | --- | --- | --- |"]
    for c in cats:
        lines.append("| %s | %d | %d | %d |" % (c["category"], c["total"], c["in_range"], c["out_of_range"]))

    attention = [c for c in cats if c["out_of_range"] > 0]
    if attention:
        lines += ["", "## Areas to focus on"]
        for c in sorted(attention, key=lambda x: -x["out_of_range"]):
            markers = ", ".join("%s (%s)" % (f["marker"], f["direction"]) for f in c["flagged"])
            lines.append("- **%s**: %d of %d outside range - %s" % (
                c["category"], c["out_of_range"], c["total"], markers))
    return "\n".join(lines)


def tool_programs(status="active"):
    programs = db.PROGRAMS if status == "all" else [p for p in db.PROGRAMS if p["status"] == status]
    if not programs:
        return "No %s Valeo programs found." % status

    lines = ["# Valeo programs (%d %s)" % (len(programs), status), ""]
    for p in programs:
        lines += [
            "## %s" % p["name"],
            "Week **%d of %d** - started %s - coach: %s" % (
                p["week"], p["total_weeks"], p["started_on"], p["coach"]),
            "Adherence: **%d%%**" % p["adherence_pct"],
            "",
            "**Goals:** " + "; ".join(p["goals"]),
            "",
            "| Metric | Start | Current | Target | Status |",
            "| --- | --- | --- | --- | --- |",
        ]
        for m in p["metrics"]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                m["metric"], m["start"], m["current"], m["target"], m["direction"]))
        lines += ["", "**Coach notes:** %s" % p["coach_notes"], ""]
    return "\n".join(lines)


def tool_appointments(include_past=False):
    lines = ["# Upcoming Valeo appointments (%d)" % len(db.APPOINTMENTS), ""]
    for a in db.APPOINTMENTS:
        lines += [
            "## %s at %s - %s" % (a["date"], a["time"], a["service"]),
            "- Clinician: %s" % a["clinician"],
            "- Location: %s" % a["location"],
            "- Status: %s" % a["status"],
            "- Preparation: %s" % a["prep"],
            "",
        ]
    if include_past:
        lines += ["# Recent services", ""]
        for s in db.RECENT_SERVICES:
            lines.append("- **%s** - %s (%s)" % (s["date"], s["service"], s["outcome"]))
    return "\n".join(lines)


def tool_supplement_plan():
    plan = db.SUPPLEMENT_PLAN
    lines = [
        "# Supplement plan for %s" % db.MEMBER["first_name"],
        "",
        "Prescribed by %s - last updated %s - next review %s" % (
            plan["prescribed_by"], plan["last_updated"], plan["next_review"]),
        "",
        "| Supplement | Dose | Timing | Why | Linked marker |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in plan["items"]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            item["name"], item["dose"], item["timing"], item["reason"], item["linked_marker"]))
    lines += ["", "**Notes:** %s" % plan["notes"]]
    return "\n".join(lines)


# =============================================================================
# Tool registry - the schemas Claude sees in tools/list
# =============================================================================

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

CATEGORY_NAMES = [c["category"] for c in db.LAB_PANEL["categories"]]

TOOLS = [
    {
        "name": "get_lab_summary",
        "title": "Lab Results Summary",
        "description": (
            "Get a high-level summary of the member's most recent Valeo blood panel: how many "
            "biomarkers were measured, how many are in optimal range, which markers are flagged, "
            "and how they are trending. Use for questions like 'how do my labs look?' or "
            "'what should I pay attention to?'. Returns summary-level data only, never raw values."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": CATEGORY_NAMES},
                    "description": "Optional. Limit the summary to these health categories. Defaults to all.",
                },
                "out_of_range_only": {
                    "type": "boolean",
                    "description": "Optional. Only report flagged markers and skip trends. Defaults to false.",
                },
                "include_trends": {
                    "type": "boolean",
                    "description": "Optional. Include change vs the previous panel. Defaults to true.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": dict(READ_ONLY, title="Lab Results Summary"),
    },
    {
        "name": "get_category_breakdown",
        "title": "Health Category Breakdown",
        "description": (
            "Get per-category counts of in-range and out-of-range biomarkers (Heart, Metabolic, "
            "Thyroid, Vitamins & Minerals, Liver, Kidney, Blood & Immunity, Hormones, Female Health). "
            "Use for 'how is my heart health?' or 'which areas need attention?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": CATEGORY_NAMES},
                    "description": "Optional. Limit to these categories. Defaults to all.",
                }
            },
            "additionalProperties": False,
        },
        "annotations": dict(READ_ONLY, title="Health Category Breakdown"),
    },
    {
        "name": "get_programs",
        "title": "Program Progress",
        "description": (
            "Get the member's Valeo wellness programs (weight loss, metabolic reset, nutrient "
            "correction) with week number, adherence, tracked metrics against target, and coach notes. "
            "Use for 'how is my program going?' or 'am I on track?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "all"],
                    "description": "Optional. Which programs to return. Defaults to 'active'.",
                }
            },
            "additionalProperties": False,
        },
        "annotations": dict(READ_ONLY, title="Program Progress"),
    },
    {
        "name": "get_appointments",
        "title": "Appointments",
        "description": (
            "Get upcoming Valeo home-visit appointments (blood draws, IV drips, physiotherapy, "
            "doctor and coach consults) including date, time, clinician, location and how to prepare. "
            "Use for 'what's my next appointment?' or 'do I need to fast?'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_past": {
                    "type": "boolean",
                    "description": "Optional. Also list recently completed services. Defaults to false.",
                }
            },
            "additionalProperties": False,
        },
        "annotations": dict(READ_ONLY, title="Appointments"),
    },
    {
        "name": "get_supplement_plan",
        "title": "Supplement Plan",
        "description": (
            "Get the member's current Valeo supplement protocol: what to take, dose, timing, the "
            "reason it was prescribed, and which biomarker it targets. Use for 'what supplements "
            "am I on?' or 'why am I taking iron?'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": dict(READ_ONLY, title="Supplement Plan"),
    },
]

HANDLERS = {
    "get_lab_summary": tool_lab_summary,
    "get_category_breakdown": tool_category_breakdown,
    "get_programs": tool_programs,
    "get_appointments": tool_appointments,
    "get_supplement_plan": tool_supplement_plan,
}


# =============================================================================
# JSON-RPC / MCP dispatch
# =============================================================================

def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications carry no id and get no response body.
    if req_id is None:
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion")
        negotiated = client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _result(req_id, {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Valeo Health", "version": SERVER_VERSION},
            "instructions": (
                "Valeo Health connector. Provides summary-level lab results, wellness program "
                "progress, appointments and supplement protocols for the connected member. "
                "Data is read-only and summary-level; it is not medical advice. "
                "DEMO BUILD: this server returns sample data for a fictional member, not real "
                "member records. Say so if the user seems to think the data is theirs."
            ),
        })

    if method == "ping":
        return _result(req_id, {})

    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})

    if method in ("resources/list", "resources/templates/list"):
        return _result(req_id, {"resources": [], "resourceTemplates": []})

    if method == "prompts/list":
        return _result(req_id, {"prompts": []})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(req_id, -32602, "Unknown tool: %s" % name)
        try:
            text = handler(**args)
        except TypeError as exc:
            return _result(req_id, {
                "content": [{"type": "text", "text": "Invalid arguments for %s: %s" % (name, exc)}],
                "isError": True,
            })
        except Exception as exc:  # surface tool errors as tool results, per MCP guidance
            log("tool error in %s: %r" % (name, exc))
            return _result(req_id, {
                "content": [{"type": "text", "text": "Valeo could not retrieve that right now: %s" % exc}],
                "isError": True,
            })
        return _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})

    return _error(req_id, -32601, "Method not found: %s" % method)


def log(message):
    sys.stderr.write("[valeo] %s\n" % message)
    sys.stderr.flush()


# =============================================================================
# HTTP layer
# =============================================================================

ALLOWED_ORIGIN_PREFIXES = ("https://claude.ai", "https://claude.com", "http://localhost", "http://127.0.0.1")


class MCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ValeoConnector/%s" % SERVER_VERSION

    def log_message(self, fmt, *args):
        log("%s - %s" % (self.address_string(), fmt % args))

    # -- helpers --------------------------------------------------------------
    def _send(self, status, body=b"", content_type=None, extra_headers=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status, payload, extra_headers=None):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json", extra_headers)

    def _send_sse(self, payload, extra_headers=None):
        """Deliver a single JSON-RPC response as a one-event SSE stream, then close."""
        body = ("event: message\ndata: %s\n\n" % json.dumps(payload)).encode("utf-8")
        self._send(200, body, "text/event-stream", extra_headers)

    def _origin_ok(self):
        """Log unrecognised origins without blocking.

        Origin checks exist to stop DNS rebinding against servers bound to localhost. This
        server is public and authless, so rejecting an unexpected Origin buys no security and
        risks refusing a legitimate client. Reinstate the block when auth lands and the server
        is only meant to be reached from known surfaces.
        """
        origin = self.headers.get("Origin")
        if origin and not any(origin.startswith(p) for p in ALLOWED_ORIGIN_PREFIXES):
            log("unrecognised Origin (allowed anyway): %s" % origin)
        return True

    # -- verbs ----------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, extra_headers={
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization, Mcp-Session-Id, MCP-Protocol-Version",
            "Access-Control-Max-Age": "86400",
        })

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/health"):
            self._send_json(200, {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION,
                                  "mcp_endpoint": MCP_PATH, "tools": [t["name"] for t in TOOLS]})
            return
        if self.path.split("?")[0] == MCP_PATH:
            # No server-initiated stream in this MVP; 405 is spec-compliant.
            log("GET %s ua=%r accept=%r -> 405" % (
                self.path, self.headers.get("User-Agent"), self.headers.get("Accept")))
            self._send(405, b"", "text/plain", {"Allow": "POST, DELETE, OPTIONS"})
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_DELETE(self):
        # Session teardown. Stateless server, so nothing to clean up.
        self._send(204 if self.path.split("?")[0] == MCP_PATH else 404)

    def do_POST(self):
        if self.path.split("?")[0] != MCP_PATH:
            self._send_json(404, {"error": "not found, use %s" % MCP_PATH})
            return
        if not self._origin_ok():
            log("rejected Origin: %s" % self.headers.get("Origin"))
            self._send_json(403, {"error": "origin not allowed"})
            return

        version = self.headers.get("MCP-Protocol-Version")
        if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
            self._send_json(400, {"error": "unsupported MCP-Protocol-Version: %s" % version})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            log("parse error, first 200 bytes: %r" % raw[:200])
            self._send_json(400, _error(None, -32700, "Parse error"))
            return

        log("POST %s ua=%r accept=%r proto=%r origin=%r session=%r method=%r" % (
            self.path,
            self.headers.get("User-Agent"),
            self.headers.get("Accept"),
            self.headers.get("MCP-Protocol-Version"),
            self.headers.get("Origin"),
            self.headers.get("Mcp-Session-Id"),
            payload.get("method") if isinstance(payload, dict) else "batch",
        ))

        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        responses = [r for r in (handle_message(m) for m in messages) if r is not None]

        # Notifications / responses only -> 202 with no body.
        if not responses:
            self._send(202)
            return

        out = responses if batch else responses[0]

        # A new session id is minted on initialize; we accept requests with or without it.
        extra = {}
        if not batch and payload.get("method") == "initialize":
            extra["Mcp-Session-Id"] = uuid.uuid4().hex

        # We never send server-initiated messages, so a single JSON object is the simplest
        # correct answer. Only fall back to SSE if the client refuses JSON.
        accept = (self.headers.get("Accept") or "")
        if "application/json" in accept or "*/*" in accept or not accept:
            self._send_json(200, out, extra)
        else:
            self._send_sse(out, extra)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    httpd = ThreadingHTTPServer((host, port), MCPHandler)
    log("Valeo connector listening on http://%s:%d%s (%d tools)" % (host, port, MCP_PATH, len(TOOLS)))
    log("health check: http://%s:%d/health" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
