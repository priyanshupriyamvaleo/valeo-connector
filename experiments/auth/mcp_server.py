#!/usr/bin/env python3
"""The Valeo MCP server, with authentication.

This is the shape server.py takes once auth lands. Three things are new:

  1. /.well-known/oauth-protected-resource tells a client where to go and sign in.
  2. A request without a usable token gets 401 plus a WWW-Authenticate header pointing
     at that document. That single response is how Claude discovers the whole flow.
  3. The member comes out of the verified token. No tool takes a member id, so there is
     no request anyone can construct that returns a different member's record.

The member's token is passed straight through to the Valeo API, so authorization stays
in one place - the API applies exactly the rules it already applies to the mobile app.
"""

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fixtures
import jwtlib

PROTOCOL_VERSION = "2026-07-28"
JWKS_URL = fixtures.AUTH_URL + "/.well-known/jwks.json"
RESOURCE_METADATA_URL = fixtures.MCP_URL + "/.well-known/oauth-protected-resource"

TOOLS = [
    {
        "name": "get_lab_summary",
        "title": "Lab Results Summary",
        "description": "Summary of the signed-in member's most recent Valeo blood panel.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Lab Results Summary", "readOnlyHint": True,
                        "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "get_profile",
        "title": "Member Profile",
        "description": "The signed-in member's name and city.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Member Profile", "readOnlyHint": True,
                        "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
]

TOOL_SCOPES = {"get_lab_summary": "read:labs", "get_profile": "read:profile"}
TOOL_ENDPOINTS = {"get_lab_summary": "/v1/me/lab-summary", "get_profile": "/v1/me/profile"}


def log(message):
    print("[mcp] %s" % message, flush=True)


def call_valeo_api(path, member_token):
    """Fetch from the Valeo API as the member, by forwarding their own token."""
    request = urllib.request.Request(
        fixtures.API_URL + path,
        headers={"Authorization": "Bearer " + member_token})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def format_lab_summary(data):
    lines = [
        "# Lab results summary for %s" % data["first_name"],
        "",
        "**Panel:** %s (collected %s)" % (data["panel"], data["collected_on"]),
        "",
        "- Biomarkers measured: **%d**" % data["total"],
        "- In optimal range: **%d**" % data["in_range"],
        "- Outside optimal range: **%d**" % data["out_of_range"],
    ]
    if data["flagged"]:
        lines += ["", "## Markers needing attention"]
        for flag in data["flagged"]:
            lines.append("- **%s** - %s, %s" % (
                flag["marker"], flag["direction"], flag["severity"]))
    return "\n".join(lines)


def format_profile(data):
    return "**%s**, member %s, based in %s." % (
        data["first_name"], data["member_id"], data["city"])


FORMATTERS = {"get_lab_summary": format_lab_summary, "get_profile": format_profile}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------ helpers
    def _json(self, status, payload, extra=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self, reason, scope=None):
        """The response that starts the whole sign-in flow.

        The 401 status is required - Claude ignores WWW-Authenticate on a 200 - and the
        resource_metadata pointer is what saves it from having to guess where the
        authorization server lives.
        """
        challenge = 'Bearer resource_metadata="%s"' % RESOURCE_METADATA_URL
        if scope:
            challenge += ', scope="%s"' % scope
        challenge += ', error="invalid_token"'
        log("401: %s" % reason)
        self._json(401, {"error": "invalid_token", "error_description": reason},
                   {"WWW-Authenticate": challenge})

    def _authenticate(self, required_scope):
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            self._unauthorized("no bearer token", required_scope)
            return None
        try:
            return jwtlib.verify(header[7:].strip(), JWKS_URL, issuer=fixtures.AUTH_URL,
                                 audience=fixtures.MCP_RESOURCE,
                                 required_scopes=[required_scope] if required_scope else ())
        except jwtlib.InvalidToken as exc:
            self._unauthorized(str(exc), required_scope)
            return None

    # --------------------------------------------------------------------- GET
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/.well-known/oauth-protected-resource":
            # RFC 9728. `resource` must match the URL a user types into Claude exactly.
            self._json(200, {
                "resource": fixtures.MCP_RESOURCE,
                "authorization_servers": [fixtures.AUTH_URL],
                "scopes_supported": fixtures.SCOPES,
                "bearer_methods_supported": ["header"],
            })
            return

        if path == "/health":
            self._json(200, {"status": "ok", "tools": [t["name"] for t in TOOLS]})
            return

        if path == "/mcp":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._json(404, {"error": "not_found"})

    # -------------------------------------------------------------------- POST
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if self.path.split("?")[0] != "/mcp":
            self._json(404, {"error": "not_found"})
            return

        try:
            message = json.loads(raw.decode())
        except ValueError:
            self._json(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}})
            return

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if request_id is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Discovery is deliberately open: a client has to be able to read it before it
        # has any token at all. It exposes nothing about any member.
        if method == "server/discover":
            self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": {
                "resultType": "complete",
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "instructions": "Valeo Health connector for the signed-in member.",
                "_meta": {"io.modelcontextprotocol/serverInfo": {
                    "name": "valeo-connector", "title": "Valeo Health", "version": "1.0.0"}},
            }})
            return

        if method == "tools/list":
            if self._authenticate(None) is None:
                return
            self._json(200, {"jsonrpc": "2.0", "id": request_id,
                             "result": {"resultType": "complete", "tools": TOOLS}})
            return

        if method == "tools/call":
            name = params.get("name")
            if name not in TOOL_SCOPES:
                self._json(404, {"jsonrpc": "2.0", "id": request_id,
                                 "error": {"code": -32601,
                                           "message": "Method not found: %s" % name}})
                return

            claims = self._authenticate(TOOL_SCOPES[name])
            if claims is None:
                return

            token = (self.headers.get("Authorization") or "")[7:].strip()
            try:
                data = call_valeo_api(TOOL_ENDPOINTS[name], token)
            except urllib.error.HTTPError as exc:
                log("Valeo API returned %s for %s" % (exc.code, name))
                self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": {
                    "resultType": "complete", "isError": True,
                    "content": [{"type": "text",
                                 "text": "Your results are temporarily unavailable."}]}})
                return

            log("%s -> %s for member %s" % (name, "ok", claims["sub"]))
            self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": {
                "resultType": "complete", "isError": False,
                "content": [{"type": "text", "text": FORMATTERS[name](data)}]}})
            return

        self._json(404, {"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": -32601, "message": "Method not found: %s" % method}})


def main():
    log("listening on %s" % fixtures.MCP_RESOURCE)
    ThreadingHTTPServer(("127.0.0.1", fixtures.MCP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
