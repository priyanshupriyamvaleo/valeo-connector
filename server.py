#!/usr/bin/env python3
"""
Valeo Connector - a minimal remote MCP server for Claude custom connectors.

Zero dependencies: standard library only, runs on Python 3.7+.
Transport: Streamable HTTP on a single /mcp endpoint.
Auth: none (demo). Add OAuth later - see README.

This is a DUAL-ERA server. Two generations of MCP are in the wild:

  modern (2026-07-28 and later) - no handshake, no sessions. Every request carries its
      protocol version, client identity and capabilities in params._meta, mirrored into
      HTTP headers. Servers MUST implement server/discover.
  legacy (2025-11-25 and earlier) - an initialize handshake opens a session.

Claude's connector client is modern. A server that only speaks legacy fails against it,
so both are implemented here and the era is chosen per request.

Run:  python3 server.py            (listens on http://127.0.0.1:8787/mcp)
      PORT=9000 HOST=0.0.0.0 python3 server.py
"""

import base64
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sample_data as db

MODERN_VERSION = "2026-07-28"
MODERN_VERSIONS = (MODERN_VERSION,)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# Advertised newest first; clients pick from this list.
ALL_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS

SERVER_NAME = "valeo-connector"
SERVER_TITLE = "Valeo Health"
SERVER_VERSION = "0.2.0"
MCP_PATH = "/mcp"

INSTRUCTIONS = (
    "Valeo Health connector. Provides summary-level lab results, wellness program progress, "
    "appointments and supplement protocols for the connected member. Data is read-only and "
    "summary-level; it is not medical advice. DEMO BUILD: this server returns sample data for "
    "a fictional member, not real member records. Say so if the user seems to think the data "
    "is theirs."
)

# Per-request metadata keys used by the modern protocol.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# JSON-RPC error codes. -32020 and -32022 are allocated by the MCP spec.
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_PARSE = -32700
ERR_HEADER_MISMATCH = -32020
ERR_UNSUPPORTED_VERSION = -32022

CACHE_TTL_MS = 3600000


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

def log(message):
    sys.stderr.write("[valeo] %s\n" % message)
    sys.stderr.flush()


def _result(req_id, result, era):
    if era == "modern":
        # Modern results declare whether they are final or awaiting client input.
        result = dict(result)
        result.setdefault("resultType", "complete")
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def handle_message(msg, era):
    """Handle one JSON-RPC message.

    Returns (http_status, response_dict_or_None). Notifications get 202 and no body.
    """
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:
        return 202, None

    # --- modern discovery: mandatory in 2026-07-28 -------------------------------
    if method == "server/discover":
        return 200, _result(req_id, {
            "supportedVersions": list(ALL_VERSIONS),
            "capabilities": {"tools": {}},
            "instructions": INSTRUCTIONS,
            "ttlMs": CACHE_TTL_MS,
            "cacheScope": "public",
            "_meta": {META_SERVER_INFO: {
                "name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION}},
        }, era)

    # --- legacy handshake --------------------------------------------------------
    if method == "initialize":
        client_version = params.get("protocolVersion")
        negotiated = client_version if client_version in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return 200, _result(req_id, {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }, "legacy")

    if method == "ping":
        return 200, _result(req_id, {}, era)

    if method == "tools/list":
        result = {"tools": TOOLS}
        if era == "modern":
            # The tool list is identical for every caller, so it is safe to cache publicly.
            result["ttlMs"] = CACHE_TTL_MS
            result["cacheScope"] = "public"
        return 200, _result(req_id, result, era)

    if method in ("resources/list", "resources/templates/list"):
        return 200, _result(req_id, {"resources": [], "resourceTemplates": []}, era)

    if method == "prompts/list":
        return 200, _result(req_id, {"prompts": []}, era)

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return 200, _error(req_id, ERR_INVALID_PARAMS, "Unknown tool: %s" % name)
        try:
            text = handler(**args)
        except TypeError as exc:
            return 200, _result(req_id, {
                "content": [{"type": "text", "text": "Invalid arguments for %s: %s" % (name, exc)}],
                "isError": True,
            }, era)
        except Exception as exc:  # surface tool errors as tool results, per MCP guidance
            log("tool error in %s: %r" % (name, exc))
            return 200, _result(req_id, {
                "content": [{"type": "text", "text": "Valeo could not retrieve that right now: %s" % exc}],
                "isError": True,
            }, era)
        return 200, _result(req_id, {
            "content": [{"type": "text", "text": text}], "isError": False}, era)

    # Modern transport requires 404 (not 200) for an unimplemented method, with the JSON-RPC
    # error in the body so a client can tell this from a 404 by an unrelated server.
    return 404, _error(req_id, ERR_METHOD_NOT_FOUND, "Method not found: %s" % method)


# =============================================================================
# HTTP layer
# =============================================================================

ALLOWED_ORIGIN_PREFIXES = ("https://claude.ai", "https://claude.com", "http://localhost", "http://127.0.0.1")


def decode_header_value(value):
    """Undo the Base64 sentinel encoding the spec defines for non-ASCII header values."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[len("=?base64?"):-len("?=")]).decode("utf-8")
        except Exception:
            return value
    return value


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
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status, payload, extra_headers=None):
        if status >= 400:
            # Don't reuse a connection after an error; it is not worth the desync risk.
            self.close_connection = True
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json", extra_headers)

    def _send_sse(self, payload, extra_headers=None):
        """Deliver a single JSON-RPC response as a one-event SSE stream, then close."""
        headers = {"X-Accel-Buffering": "no"}
        headers.update(extra_headers or {})
        body = ("event: message\ndata: %s\n\n" % json.dumps(payload)).encode("utf-8")
        self._send(200, body, "text/event-stream", headers)

    def _read_body(self):
        """Read the request body completely, honouring chunked transfer encoding.

        This MUST happen before any response is written. Replying while the body is still
        in the socket desyncs a keep-alive connection: the server then parses the leftover
        body bytes as the next HTTP request line and answers 501 to nonsense.
        """
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            chunks = []
            while True:
                line = self.rfile.readline(65536).strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline(65536)  # trailing CRLF
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline(65536)  # CRLF after each chunk
            return b"".join(chunks)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _note_origin(self):
        """Log an unrecognised Origin without blocking.

        Origin checks exist to stop DNS rebinding against servers bound to localhost. This
        server is public and authless, so refusing an unexpected Origin buys no security and
        risks rejecting a legitimate client. Reinstate the block when auth lands.
        """
        origin = self.headers.get("Origin")
        if origin and not any(origin.startswith(p) for p in ALLOWED_ORIGIN_PREFIXES):
            log("unrecognised Origin (allowed anyway): %s" % origin)

    def _header_body_mismatch(self, msg):
        """Return a complaint string if the mirrored headers disagree with the body.

        The modern transport mirrors method and name into headers so intermediaries can route
        without parsing the body. If the two disagree, a load balancer and this server would be
        acting on different values, so the request must be refused.

        Only a genuine mismatch is rejected. A missing header is tolerated: strictness there
        could only turn a working client into a broken one.
        """
        params = msg.get("params") or {}
        header_method = self.headers.get("Mcp-Method")
        if header_method and msg.get("method") and header_method != msg.get("method"):
            return "Mcp-Method header %r does not match body method %r" % (
                header_method, msg.get("method"))

        header_name = decode_header_value(self.headers.get("Mcp-Name"))
        body_name = params.get("name") or params.get("uri")
        if header_name and body_name and header_name != body_name:
            return "Mcp-Name header %r does not match body value %r" % (header_name, body_name)
        return None

    # -- verbs ----------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, extra_headers={
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": ("Content-Type, Accept, Authorization, "
                                             "MCP-Protocol-Version, Mcp-Method, Mcp-Name"),
            "Access-Control-Max-Age": "86400",
        })

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if path in ("/", "/health"):
            self._send(200, b"", "application/json")
        elif path == MCP_PATH:
            self._send(405, b"", "text/plain", {"Allow": "POST, OPTIONS"})
        else:
            self._send(404)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/health"):
            self._send_json(200, {
                "status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION,
                "mcp_endpoint": MCP_PATH, "protocol_versions": list(ALL_VERSIONS),
                "tools": [t["name"] for t in TOOLS]})
            return
        if path == MCP_PATH:
            # The modern revision removed the GET stream; 405 is the specified answer.
            log("GET %s ua=%r -> 405" % (self.path, self.headers.get("User-Agent")))
            self._send(405, b"", "text/plain", {"Allow": "POST, OPTIONS"})
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_DELETE(self):
        # Sessions are gone in the modern revision; DELETE has nothing to terminate.
        self._send(405, b"", "text/plain", {"Allow": "POST, OPTIONS"})

    def do_POST(self):
        # Always drain the body first - see _read_body.
        raw = self._read_body()

        if self.path.split("?")[0] != MCP_PATH:
            self._send_json(404, {"error": "not found, use %s" % MCP_PATH})
            return
        self._note_origin()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            log("parse error, first 200 bytes: %r" % raw[:200])
            self._send_json(400, _error(None, ERR_PARSE, "Parse error"))
            return

        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        first = messages[0] if messages and isinstance(messages[0], dict) else {}
        params = first.get("params") or {}
        meta = params.get("_meta") or {}

        header_version = self.headers.get("MCP-Protocol-Version")
        body_version = meta.get(META_VERSION)
        client_info = meta.get(META_CLIENT_INFO) or {}

        log("POST %s client=%r ua=%r method=%r proto=%r/%r accept=%r" % (
            self.path,
            client_info.get("name"),
            self.headers.get("User-Agent"),
            first.get("method") if not batch else "batch",
            header_version, body_version,
            self.headers.get("Accept"),
        ))

        # The header must agree with the body, or intermediaries and this server would be
        # acting on different values.
        if header_version and body_version and header_version != body_version:
            self._send_json(400, _error(
                first.get("id"), ERR_HEADER_MISMATCH,
                "Header mismatch: MCP-Protocol-Version %r does not match _meta %r" % (
                    header_version, body_version)))
            return

        mismatch = self._header_body_mismatch(first) if not batch else None
        if mismatch:
            self._send_json(400, _error(first.get("id"), ERR_HEADER_MISMATCH,
                                        "Header mismatch: %s" % mismatch))
            return

        requested = body_version or header_version
        if requested and requested not in ALL_VERSIONS:
            # Say what we do support so the client can retry rather than give up.
            self._send_json(400, _error(
                first.get("id"), ERR_UNSUPPORTED_VERSION, "Unsupported protocol version",
                {"supported": list(ALL_VERSIONS), "requested": requested}))
            return

        # An initialize request selects legacy semantics; per-request _meta selects modern.
        if first.get("method") == "initialize":
            era = "legacy"
        elif requested in MODERN_VERSIONS or first.get("method") == "server/discover":
            era = "modern"
        elif requested in LEGACY_VERSIONS:
            era = "legacy"
        else:
            era = "modern"

        results = [handle_message(m, era) for m in messages]
        responses = [r for _, r in results if r is not None]
        status = max([s for s, _ in results] or [200])

        if not responses:
            self._send(202)
            return

        out = responses if batch else responses[0]

        extra = {}
        if era == "legacy" and not batch and first.get("method") == "initialize":
            # Legacy clients expect a session id. Modern requests never get one.
            extra["Mcp-Session-Id"] = uuid.uuid4().hex
            extra["Access-Control-Expose-Headers"] = "Mcp-Session-Id"

        accept = (self.headers.get("Accept") or "")
        if "application/json" in accept or "*/*" in accept or not accept:
            self._send_json(status, out, extra)
        else:
            self._send_sse(out, extra)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    httpd = ThreadingHTTPServer((host, port), MCPHandler)
    log("Valeo connector %s listening on http://%s:%d%s (%d tools)" % (
        SERVER_VERSION, host, port, MCP_PATH, len(TOOLS)))
    log("protocol versions: %s" % ", ".join(ALL_VERSIONS))
    log("health check: http://%s:%d/health" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
