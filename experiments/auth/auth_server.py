#!/usr/bin/env python3
"""Valeo's authorization server, standing in for Cognito or whatever you pick later.

Implements the parts Claude actually uses:

  /.well-known/oauth-authorization-server  discovery (RFC 8414)
  /.well-known/jwks.json                   the public key tokens are verified against
  /register                                dynamic client registration (RFC 7591)
  /authorize                               the member's login and consent screen
  /token                                   code -> tokens, and refresh -> new tokens

State is kept in memory. Restarting the server forgets every code and refresh token,
which is fine for an experiment and is exactly what you must not do in production.
"""

import hashlib
import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fixtures
import jwtlib

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_PATH = os.path.join(HERE, "signing-key.pem")
KEY_ID = "valeo-auth-2026"

ISSUER = fixtures.AUTH_URL
JWKS_URL = ISSUER + "/.well-known/jwks.json"

CLIENTS = {}        # client_id -> registration
CODES = {}          # code -> {member, client_id, challenge, scope, expires}
REFRESH_TOKENS = {}  # refresh token -> {member, client_id, scope}

LOGIN_PAGE = """<!doctype html>
<title>Sign in to Valeo</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; background: #FFF9E6;
        display: grid; place-items: center; min-height: 100vh; margin: 0; color: #1B1815; }}
 .card {{ background: #fff; padding: 36px 40px; border-radius: 14px; width: 340px;
          box-shadow: 0 2px 20px rgba(0,0,0,.08); }}
 h1 {{ font-size: 19px; margin: 0 0 6px; }}
 p {{ color: #6B6459; font-size: 14px; margin: 0 0 22px; line-height: 1.5; }}
 label {{ display: block; font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
          color: #6B6459; margin-bottom: 6px; }}
 select, input {{ width: 100%; padding: 10px; font-size: 15px; border: 1px solid #E5E1D8;
                  border-radius: 8px; margin-bottom: 16px; box-sizing: border-box;
                  background: #fff; color: #1B1815; }}
 button {{ width: 100%; padding: 12px; font-size: 15px; font-weight: 600; border: 0;
           border-radius: 8px; background: #FFDB58; color: #4A3B05; cursor: pointer; }}
 .scopes {{ background: #FBF9F2; border-radius: 8px; padding: 12px 14px; margin-bottom: 20px; }}
 .scopes li {{ font-size: 13px; color: #4A453D; margin: 3px 0; }}
</style>
<div class="card">
  <h1>Sign in to Valeo</h1>
  <p>{client_name} is asking to see your health results.</p>
  <div class="scopes"><ul>{scope_items}</ul></div>
  <form method="post" action="/authorize">
    <label for="username">Member</label>
    <select name="username" id="username">{options}</select>
    <label for="password">Password</label>
    <input type="password" name="password" id="password" autocomplete="off">
    {hidden}
    <button type="submit">Allow access</button>
  </form>
</div>
"""

SCOPE_LABELS = {
    "read:labs": "See a summary of your lab results",
    "read:profile": "See your name and city",
    "offline_access": "Stay connected without signing in again",
}


def log(message):
    print("[auth] %s" % message, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # the explicit log() calls below are the interesting ones

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

    def _html(self, status, markup):
        body = markup.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # --------------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/.well-known/oauth-authorization-server":
            self._json(200, {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "registration_endpoint": ISSUER + "/register",
                "jwks_uri": JWKS_URL,
                "scopes_supported": fixtures.SCOPES,
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            })
            return

        if parsed.path == "/.well-known/jwks.json":
            self._json(200, jwtlib.jwks_document(KEY_PATH, KEY_ID))
            return

        if parsed.path == "/authorize":
            return self._authorize_page(query)

        self._json(404, {"error": "not_found"})

    def _authorize_page(self, query):
        client = CLIENTS.get(query.get("client_id", ""))
        if not client:
            self._json(400, {"error": "invalid_client"})
            return
        if query.get("code_challenge_method") != "S256":
            # PKCE is mandatory. Claude always sends S256, so refusing anything else
            # costs nothing and closes the code-interception attack.
            self._json(400, {"error": "invalid_request",
                             "error_description": "S256 PKCE required"})
            return

        requested = (query.get("scope") or "").split()
        hidden = "".join(
            '<input type="hidden" name="%s" value="%s">' % (
                key, urllib.parse.quote(query.get(key, ""), safe=""))
            for key in ("client_id", "redirect_uri", "state", "code_challenge",
                        "code_challenge_method", "scope"))
        options = "".join(
            '<option value="%s">%s</option>' % (username, member["first_name"])
            for username, member in fixtures.MEMBERS.items())
        scope_items = "".join(
            "<li>%s</li>" % SCOPE_LABELS.get(scope, scope) for scope in requested)

        log("showing consent screen to a member for client %r" % client["client_name"])
        self._html(200, LOGIN_PAGE.format(
            client_name=client["client_name"], options=options,
            scope_items=scope_items, hidden=hidden))

    # -------------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        raw = self._body()

        if parsed.path == "/register":
            return self._register(raw)
        if parsed.path == "/authorize":
            return self._authorize_submit(raw)
        if parsed.path == "/token":
            return self._token(raw)
        self._json(404, {"error": "not_found"})

    def _register(self, raw):
        """Dynamic client registration. Claude calls this on a fresh connection."""
        try:
            request = json.loads(raw.decode() or "{}")
        except ValueError:
            self._json(400, {"error": "invalid_client_metadata"})
            return
        client_id = "client_" + secrets.token_hex(8)
        CLIENTS[client_id] = {
            "client_id": client_id,
            "client_name": request.get("client_name", "Unnamed client"),
            "redirect_uris": request.get("redirect_uris", []),
        }
        log("registered client %r as %s" % (CLIENTS[client_id]["client_name"], client_id))
        self._json(201, {
            "client_id": client_id,
            "client_name": CLIENTS[client_id]["client_name"],
            "redirect_uris": CLIENTS[client_id]["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        })

    def _authorize_submit(self, raw):
        form = {k: urllib.parse.unquote(v)
                for k, v in urllib.parse.parse_qsl(raw.decode())}
        username = form.get("username", "")
        member = fixtures.MEMBERS.get(username)

        if not member or form.get("password") != member["password"]:
            log("failed sign-in for %r" % username)
            self._html(401, "<p>Wrong member or password.</p>")
            return

        redirect_uri = form.get("redirect_uri", "")
        client = CLIENTS.get(form.get("client_id", ""))
        if not client or redirect_uri not in client["redirect_uris"]:
            # A redirect_uri that was not registered is how stolen codes get delivered
            # somewhere else. Never redirect to it, and never echo it back.
            self._json(400, {"error": "invalid_redirect_uri"})
            return

        code = secrets.token_urlsafe(24)
        CODES[code] = {
            "member": username,
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "challenge": form.get("code_challenge", ""),
            "scope": form.get("scope", ""),
            "expires": time.time() + 60,
        }
        log("%s signed in and consented; issued an authorization code" % member["first_name"])
        separator = "&" if "?" in redirect_uri else "?"
        self._redirect("%s%scode=%s&state=%s" % (
            redirect_uri, separator, code, urllib.parse.quote(form.get("state", ""))))

    def _token(self, raw):
        form = dict(urllib.parse.parse_qsl(raw.decode()))
        grant = form.get("grant_type")

        if grant == "authorization_code":
            record = CODES.pop(form.get("code", ""), None)
            if not record or record["expires"] < time.time():
                self._json(400, {"error": "invalid_grant"})
                return
            if record["client_id"] != form.get("client_id"):
                self._json(400, {"error": "invalid_grant"})
                return
            # PKCE: the verifier must hash to the challenge sent at /authorize.
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            if jwtlib.b64url_encode(digest) != record["challenge"]:
                log("PKCE verification failed - refusing the code exchange")
                self._json(400, {"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"})
                return
            member, scope = record["member"], record["scope"]

        elif grant == "refresh_token":
            record = REFRESH_TOKENS.pop(form.get("refresh_token", ""), None)
            if not record:
                # RFC 6749 says invalid_grant specifically; Claude keys its retry on it.
                self._json(400, {"error": "invalid_grant"})
                return
            member, scope = record["member"], record["scope"]
            log("refreshed tokens for %s" % fixtures.MEMBERS[member]["first_name"])

        else:
            self._json(400, {"error": "unsupported_grant_type"})
            return

        self._json(200, self._issue(member, scope, form.get("client_id", "")),
                   {"Cache-Control": "no-store"})

    def _issue(self, username, scope, client_id):
        member = fixtures.MEMBERS[username]
        now = int(time.time())
        access_token = jwtlib.sign({
            "iss": ISSUER,
            "sub": member["member_id"],
            # Two audiences so the same token works at the connector and, passed through,
            # at the Valeo API behind it. The alternative is a token exchange at the
            # boundary; this is the simpler shape and is a legitimate one.
            "aud": [fixtures.MCP_RESOURCE, fixtures.API_URL],
            "scope": scope,
            "iat": now,
            "exp": now + fixtures.ACCESS_TOKEN_TTL,
        }, KEY_PATH, KEY_ID)

        response = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": fixtures.ACCESS_TOKEN_TTL,
            "scope": scope,
        }
        if "offline_access" in scope.split():
            refresh = secrets.token_urlsafe(32)
            REFRESH_TOKENS[refresh] = {"member": username, "client_id": client_id,
                                       "scope": scope}
            response["refresh_token"] = refresh
        return response


def main():
    if not os.path.exists(KEY_PATH):
        log("generating a signing key")
        jwtlib.generate_key(KEY_PATH)
    log("listening on %s" % ISSUER)
    ThreadingHTTPServer(("127.0.0.1", fixtures.AUTH_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
