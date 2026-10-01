# Auth experiment

End-to-end OAuth for the Valeo connector, **with Claude taken out of the loop**, so a
failure can only be your auth and never the protocol.

```bash
./run.sh              # starts everything, runs 30 checks, tears it all down
./run.sh --manual     # pauses so you can sign in through the browser yourself
```

No installs, no accounts, no network. Three servers start on localhost, a test client
exercises them, everything is killed on exit.

## What is running

| Port | Piece | Standing in for |
| --- | --- | --- |
| 9002 | `auth_server.py` | Valeo's identity provider — Cognito, or whatever you pick |
| 9001 | `valeo_api.py` | Valeo's member API and the database behind it |
| 8788 | `mcp_server.py` | The connector, with authentication added |
| — | `test_client.py` | **Claude.** It does exactly what Claude does |

Two members exist, with deliberately different results: Sundeep has 68 biomarkers, Meera
has 42. That difference is the point. A test with one member passes even when auth is
completely broken and everyone is being handed the same record.

## What the 30 checks prove

1. **An unauthenticated call is refused** with `401` and a `WWW-Authenticate` header
   pointing at the resource metadata. That one response is how Claude discovers the
   entire sign-in flow.
2. **Discovery works** — the client finds the authorization server knowing nothing but
   that header.
3. **Dynamic client registration** returns a `client_id`, as it does for Claude.
4. **The consent screen renders** and a wrong password is refused.
5. **PKCE guards the exchange** — a stolen authorization code is useless without the
   verifier, and a failed attempt burns the code.
6. **Two members get two different sets of results**, and neither answer leaks the other.
7. **No tool accepts a member id**, so no request can be aimed at someone else.
8. **Bad tokens fail**: a token edited to name another member, a garbage token, and a
   token missing the `read:labs` scope are all rejected — while a profile-only token
   still reads the profile it was granted.
9. **Refresh works** without the member signing in again.

## Why this makes the Claude integration simple

Claude performs the same sequence this client performs: the same `401`, the same
discovery, the same redirect to your login page, the same code-for-token exchange, the
same `Authorization: Bearer` on every call. Two things change when you swap it in:

- register `https://claude.ai/api/mcp/auth_callback` as a redirect URI
- serve all of it from real `https://` URLs instead of `127.0.0.1`

Nothing about the connector's logic changes.

## Taking it to production

What carries over as-is:

- `mcp_server.py` — the `401` shape, the resource metadata document, token verification,
  resolving the member from the token, forwarding that token to the API
- `jwtlib.verify()` — RS256 against a JWKS endpoint is exactly what Cognito needs

What gets replaced:

- `auth_server.py` → Cognito or your existing member login. Note it does **not** support
  dynamic client registration, so you register one client up front and supply the
  `client_id` when adding the connector
- `valeo_api.py` → the real API. Add a purpose-built `/v1/me/lab-summary` endpoint rather
  than having the connector fetch raw biomarkers and summarise them
- `fixtures.py` → deleted

## Notes

- **`signing-key.pem` is generated on first run and is git-ignored.** It never leaves this
  directory. Production keys belong in Secrets Manager or Cognito's own key management.
- Tokens are signed with RS256. Signing shells out to `openssl`; **verification is pure
  Python** — an RSA public key operation is just `pow()` — so the half you actually ship
  inside the connector has no dependencies.
- Access tokens live 300 seconds so the refresh path can be demonstrated.
- The two test accounts and their passwords are in `fixtures.py`. They exist only on
  localhost and guard nothing.
