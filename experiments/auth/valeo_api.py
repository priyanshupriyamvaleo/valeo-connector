#!/usr/bin/env python3
"""Valeo's member API, standing in for the real backend and its database.

The only thing that matters here: every route resolves "me" from the token's subject.
There is no member id in any path or query string, so there is no request a caller can
construct that returns somebody else's record.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fixtures
import jwtlib

JWKS_URL = fixtures.AUTH_URL + "/.well-known/jwks.json"


def log(message):
    print("[valeo-api] %s" % message, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _member(self, required_scope):
        """Resolve the caller from their token, or answer 401 and return None."""
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            self._json(401, {"error": "missing_token"})
            return None
        try:
            claims = jwtlib.verify(
                header[7:].strip(), JWKS_URL, issuer=fixtures.AUTH_URL,
                audience=fixtures.API_URL, required_scopes=[required_scope])
        except jwtlib.InvalidToken as exc:
            log("rejected a token: %s" % exc)
            self._json(401, {"error": "invalid_token", "detail": str(exc)})
            return None

        username = fixtures.BY_MEMBER_ID.get(claims["sub"])
        if not username:
            self._json(404, {"error": "unknown_member"})
            return None
        return username

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            self._json(200, {"status": "ok"})
            return

        if path == "/v1/me/lab-summary":
            username = self._member("read:labs")
            if not username:
                return
            member = fixtures.MEMBERS[username]
            log("served lab summary for %s" % member["first_name"])
            self._json(200, dict(member["lab_summary"], first_name=member["first_name"]))
            return

        if path == "/v1/me/profile":
            username = self._member("read:profile")
            if not username:
                return
            member = fixtures.MEMBERS[username]
            log("served profile for %s" % member["first_name"])
            self._json(200, {"first_name": member["first_name"], "city": member["city"],
                             "member_id": member["member_id"]})
            return

        self._json(404, {"error": "not_found"})


def main():
    log("listening on %s" % fixtures.API_URL)
    ThreadingHTTPServer(("127.0.0.1", fixtures.API_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
