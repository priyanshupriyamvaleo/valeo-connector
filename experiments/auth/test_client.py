#!/usr/bin/env python3
"""The experiment. This script plays exactly the role Claude plays.

It starts knowing one thing - the connector's URL - and from there discovers where to
sign in, registers itself, runs a member through consent, exchanges the code for a
token, and calls the tools. Every step is the step Claude takes.

Run it through run.sh, or directly once the three servers are up:

    python3 test_client.py
    python3 test_client.py --manual     # open the consent screen in a browser
"""

import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

import fixtures
import jwtlib

MANUAL = "--manual" in sys.argv
passed = failed = 0


# ----------------------------------------------------------------- HTTP helpers

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop at the 302 so we can read the authorization code out of the Location."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http(url, method="GET", data=None, headers=None, form=False):
    """Returns (status, headers, parsed_body_or_text)."""
    body = None
    head = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            head["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            head["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, method=method, headers=head)
    try:
        response = OPENER.open(request, timeout=15)
        status, response_headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        status, response_headers, raw = exc.code, exc.headers, exc.read()

    text = raw.decode(errors="replace")
    try:
        return status, response_headers, json.loads(text)
    except ValueError:
        return status, response_headers, text


def mcp(method, params=None, token=None):
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if params and params.get("name"):
        headers["Mcp-Name"] = params["name"]
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": dict(params or {})}
    payload["params"]["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "auth-experiment", "version": "1.0"},
    }
    return http(fixtures.MCP_RESOURCE, "POST", payload, headers)


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("  \033[32mPASS\033[0m  %s %s" % (label, detail))
    else:
        failed += 1
        print("  \033[31mFAIL\033[0m  %s %s" % (label, detail))
    return bool(condition)


def step(title):
    print("\n\033[1m%s\033[0m" % title)


# ------------------------------------------------------------------- OAuth flow

def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = jwtlib.b64url_encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def authorize(client_id, username, challenge, scopes, password=None):
    """Drive the consent screen and come back with an authorization code.

    A browser would do this with a human clicking. We submit the same form, then read the
    code out of the redirect the server sends back - which is what the browser would hand
    to Claude's callback URL.
    """
    member = fixtures.MEMBERS[username]
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": fixtures.REDIRECT_URI, "scope": " ".join(scopes),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    authorize_url = fixtures.AUTH_URL + "/authorize?" + query

    status, _, page = http(authorize_url)
    if status != 200:
        return None, None, status

    if MANUAL:
        import webbrowser
        print("\n    Opening the consent screen. Sign in as %s." % member["first_name"])
        print("    The password is in fixtures.py under MEMBERS[%r].\n" % username)
        webbrowser.open(authorize_url)
        code = input("    Paste the ?code= value from the address bar: ").strip()
        return code, state, 200

    status, headers, _ = http(fixtures.AUTH_URL + "/authorize", "POST", {
        "username": username,
        "password": password if password is not None else member["password"],
        "client_id": client_id, "redirect_uri": fixtures.REDIRECT_URI,
        "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "scope": " ".join(scopes),
    }, form=True)

    if status != 302:
        return None, state, status
    location = headers.get("Location", "")
    returned = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
    return returned.get("code"), returned.get("state"), status


def exchange(code, verifier, client_id):
    return http(fixtures.AUTH_URL + "/token", "POST", {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": fixtures.REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    }, form=True)


def sign_in(client_id, username, scopes):
    """The whole flow for one member, returning the token response."""
    verifier, challenge = pkce_pair()
    code, _, _ = authorize(client_id, username, challenge, scopes)
    if not code:
        return None
    status, _, tokens = exchange(code, verifier, client_id)
    return tokens if status == 200 else None


# ------------------------------------------------------------------ the experiment

print("\033[1mValeo auth experiment\033[0m")
print("Claude is not involved. This client does what Claude would do.\n")
print("  connector   %s" % fixtures.MCP_RESOURCE)
print("  auth server %s" % fixtures.AUTH_URL)
print("  valeo api   %s" % fixtures.API_URL)

# --- 1. A client that has never seen this server tries to use it ---------------
step("1. An unauthenticated call is refused, and says how to fix it")

status, headers, body = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}})
check("tools/call without a token is refused", status == 401, "(http %s)" % status)

challenge_header = headers.get("WWW-Authenticate", "")
check("the 401 carries a WWW-Authenticate challenge", "Bearer" in challenge_header)
check("  it points at the protected resource metadata",
      "resource_metadata=" in challenge_header,
      "\n          %s" % challenge_header if challenge_header else "")

# --- 2. Discovery: from that one header to the login page ---------------------
step("2. Discovery - the client finds the sign-in flow knowing only the 401")

metadata_url = challenge_header.split('resource_metadata="')[1].split('"')[0]
status, _, resource_meta = http(metadata_url)
check("protected resource metadata is readable", status == 200)
check("  `resource` matches the connector URL exactly",
      resource_meta.get("resource") == fixtures.MCP_RESOURCE,
      "(%s)" % resource_meta.get("resource"))
check("  it names the authorization server",
      fixtures.AUTH_URL in resource_meta.get("authorization_servers", []))

status, _, auth_meta = http(resource_meta["authorization_servers"][0]
                            + "/.well-known/oauth-authorization-server")
check("authorization server metadata is readable", status == 200)
check("  it advertises S256 PKCE",
      "S256" in auth_meta.get("code_challenge_methods_supported", []))
check("  it exposes a registration endpoint",
      bool(auth_meta.get("registration_endpoint")))

# --- 3. Register, the way Claude registers itself -----------------------------
step("3. The client registers itself (dynamic client registration)")

status, _, registration = http(auth_meta["registration_endpoint"], "POST", {
    "client_name": "Claude", "redirect_uris": [fixtures.REDIRECT_URI],
    "grant_types": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_method": "none",
})
client_id = (registration or {}).get("client_id")
check("registration returns a client_id", status == 201 and bool(client_id),
      "(%s)" % client_id)

# --- 4. The consent screen, and a wrong password ------------------------------
step("4. The member signs in")

verifier, challenge = pkce_pair()
status, _, page = http(fixtures.AUTH_URL + "/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": client_id, "redirect_uri": fixtures.REDIRECT_URI,
    "scope": " ".join(fixtures.SCOPES), "state": "abc",
    "code_challenge": challenge, "code_challenge_method": "S256"}))
check("the consent screen renders", status == 200 and "Sign in to Valeo" in str(page))
check("  it names the client asking for access", "Claude" in str(page))

code, _, status = authorize(client_id, "sundeep", challenge, fixtures.SCOPES,
                            password="not-the-password")
check("a wrong password is refused", code is None and status == 401, "(http %s)" % status)

# --- 5. PKCE actually guards the exchange -------------------------------------
step("5. PKCE - a stolen code is useless without the verifier")

verifier, challenge = pkce_pair()
code, _, _ = authorize(client_id, "sundeep", challenge, fixtures.SCOPES)
check("signing in produces an authorization code", bool(code))

wrong_verifier, _ = pkce_pair()
status, _, body = exchange(code, wrong_verifier, client_id)
check("exchanging the code with the wrong verifier is refused",
      status == 400 and (body or {}).get("error") == "invalid_grant", "(http %s)" % status)

status, _, body = exchange(code, verifier, client_id)
check("  and that code is now burned, even for the right verifier",
      status == 400, "(http %s)" % status)

# --- 6. A real sign-in, for two different members -----------------------------
step("6. Two members, two different sets of results")

sundeep_tokens = sign_in(client_id, "sundeep", fixtures.SCOPES)
check("Sundeep signs in and receives an access token",
      bool((sundeep_tokens or {}).get("access_token")))
check("  and a refresh token, because offline_access was granted",
      bool((sundeep_tokens or {}).get("refresh_token")))

meera_tokens = sign_in(client_id, "meera", fixtures.SCOPES)
check("Meera signs in and receives an access token",
      bool((meera_tokens or {}).get("access_token")))

status, _, body = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}},
                      token=sundeep_tokens["access_token"])
sundeep_text = (((body or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
check("Sundeep's token returns Sundeep's results",
      status == 200 and "Sundeep" in sundeep_text and "**68**" in sundeep_text,
      "(68 markers)" if "**68**" in sundeep_text else "(got: %s)" % sundeep_text[:60])

status, _, body = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}},
                      token=meera_tokens["access_token"])
meera_text = (((body or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
check("Meera's token returns Meera's results",
      status == 200 and "Meera" in meera_text and "**42**" in meera_text,
      "(42 markers)" if "**42**" in meera_text else "(got: %s)" % meera_text[:60])

check("  the two members got genuinely different data",
      bool(sundeep_text) and bool(meera_text) and sundeep_text != meera_text)
check("  neither answer leaked the other member",
      "Meera" not in sundeep_text and "Sundeep" not in meera_text)

# --- 7. There is no way to ask for someone else -------------------------------
step("7. No tool can be pointed at another member")

status, _, body = mcp("tools/list", token=sundeep_tokens["access_token"])
tools = ((body or {}).get("result") or {}).get("tools") or []
parameters = [key for tool in tools for key in tool["inputSchema"].get("properties", {})]
check("no tool accepts a member id",
      not any(k in parameters for k in ("member_id", "user_id", "patient_id", "sub")),
      "(parameters: %s)" % (parameters or "none"))

# --- 8. Bad tokens are rejected -----------------------------------------------
step("8. Tokens that should not work, do not work")

head, claims, signature = sundeep_tokens["access_token"].split(".")
tampered_claims = json.loads(jwtlib.b64url_decode(claims))
tampered_claims["sub"] = fixtures.MEMBERS["meera"]["member_id"]
forged = "%s.%s.%s" % (head, jwtlib.b64url_encode(
    json.dumps(tampered_claims, separators=(",", ":")).encode()), signature)

status, _, _ = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}}, token=forged)
check("a token edited to name another member is rejected", status == 401,
      "(http %s)" % status)

status, _, _ = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}},
                   token="not-even-a-token")
check("a garbage token is rejected", status == 401, "(http %s)" % status)

limited = sign_in(client_id, "sundeep", ["read:profile"])
status, _, _ = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}},
                   token=limited["access_token"])
check("a token without read:labs cannot read labs", status == 401, "(http %s)" % status)

status, _, body = mcp("tools/call", {"name": "get_profile", "arguments": {}},
                      token=limited["access_token"])
check("  but it can still read the profile it was granted", status == 200,
      "(http %s)" % status)

# --- 9. Refresh ----------------------------------------------------------------
step("9. Refresh - the member does not sign in again")

status, _, refreshed = http(fixtures.AUTH_URL + "/token", "POST", {
    "grant_type": "refresh_token", "refresh_token": sundeep_tokens["refresh_token"],
    "client_id": client_id}, form=True)
check("the refresh token returns a new access token",
      status == 200 and bool((refreshed or {}).get("access_token")))

status, _, body = mcp("tools/call", {"name": "get_lab_summary", "arguments": {}},
                      token=(refreshed or {}).get("access_token", ""))
text = (((body or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
check("  the refreshed token works and still resolves to Sundeep",
      status == 200 and "Sundeep" in text)

# --------------------------------------------------------------------- verdict
print("\n" + "-" * 62)
print("\033[1m%d passed, %d failed\033[0m" % (passed, failed))
if not failed:
    print("""
The flow works end to end. Claude does exactly what this script just did, so
pointing it at a connector built this way needs two changes and nothing else:

  - register https://claude.ai/api/mcp/auth_callback as a redirect URI
  - serve all of this from real https URLs instead of 127.0.0.1
""")
sys.exit(1 if failed else 0)
