# Valeo Connector (MCP) — MVP

A minimal **remote MCP server** that turns Valeo Health member data into a Claude connector,
the same way the Function Health connector works.

- **Zero dependencies.** Standard-library Python only. Runs on the stock macOS `python3`.
- **Transport:** Streamable HTTP (MCP spec `2025-06-18`), single `/mcp` endpoint.
- **Auth:** none yet (demo). All data comes from `sample_data.py`.
- **Read-only.** Every tool is annotated `readOnlyHint: true`.

## Files

| File | What it is |
| --- | --- |
| `server.py` | The whole connector: HTTP transport + JSON-RPC dispatch + 5 tools |
| `sample_data.py` | The **only** fake part. Swap these lookups for real Valeo API calls to go live |
| `test_server.py` | 22-check smoke test of the protocol and every tool |
| `.mcp.json` | Project-scoped config so Claude Code picks up the local server automatically |
| `run.sh` | `./run.sh` to start it |

## Tools

| Tool | Answers questions like | Parameters |
| --- | --- | --- |
| `get_lab_summary` | "How do my labs look?" "What needs attention?" | `categories`, `out_of_range_only`, `include_trends` |
| `get_category_breakdown` | "How is my heart health?" "Which areas are off?" | `categories` |
| `get_programs` | "How is my Metabolic Reset going?" "Am I on track?" | `status` |
| `get_appointments` | "What's my next appointment?" "Do I need to fast?" | `include_past` |
| `get_supplement_plan` | "What supplements am I on and why?" | — |

Health categories in the sample panel: Heart, Metabolic, Vitamins & Minerals, Thyroid, Liver,
Kidney, Blood & Immunity, Hormones, Female Health.

Like Function Health, the connector deliberately exposes **summary-level data only** —
counts, categories, flags and directions ("Vitamin D, low, significant"), never raw lab values.

## Run it

```bash
./run.sh
```

Then in another terminal:

```bash
python3 test_server.py
```

Health check: <http://127.0.0.1:8787/health>

## Demo path 1 — Claude Code (fastest, ~30 seconds)

`.mcp.json` already points at `http://127.0.0.1:8787/mcp`. Start the server, open Claude Code
in this folder, approve the `valeo` server when prompted, then ask:

> How do my labs look, and how is my program going?

## Demo path 2 — anyone with a Claude subscription (public URL)

Claude's servers call your endpoint, so it needs a **stable public HTTPS URL**; `localhost` and
tunnels that die on restart will not do. Deploy the repo, then anyone on a paid Claude plan
(Pro, Max, Team, Enterprise) can add it themselves:

**Claude → Settings → Connectors → Add custom connector** → paste

```
https://<your-host>/mcp
```

### Deploying

`render.yaml` and `Dockerfile` are both in the repo and run `server.py` unchanged.

**Render (no CLI needed, free):**

1. Push this repo to GitHub.
2. render.com → **New → Blueprint** → pick the repo → Apply. It reads `render.yaml`.
3. You get `https://valeo-connector.onrender.com`. The connector URL is that plus `/mcp`.

Free instances sleep after ~15 minutes idle, so the first call after a nap takes 30–60s
(Claude's tool timeout is 300s, so it still works — it just feels slow). The $7 plan or Valeo's
own infrastructure removes that.

**Any container host** (Cloud Run, Fly, Railway) works off the `Dockerfile`. Set `HOST=0.0.0.0`;
the server reads `PORT` from the environment.

Eventually this should live on Valeo's own domain — Function Health serves theirs from
`https://services.functionhealth.com/ai-chat/mcp`.

### Two things to know before sharing the URL

1. **There is no auth yet, so every user sees the same fictional member.** That is fine for a
   demo and it is stated in the server's own instructions to Claude. It is the one thing that
   must change before real member data goes anywhere near this.
2. **Being listed in Claude's Connectors Directory is a separate step** — an application to
   Anthropic. Without it the connector still works; users just add the URL by hand instead of
   finding it in the directory.

## What to add after the MVP

1. **OAuth 2.0** so each member sees only their own data. Claude supports Dynamic Client
   Registration; the callback for hosted surfaces is `https://claude.ai/api/mcp/auth_callback`.
   Serve `/.well-known/oauth-protected-resource` (RFC 9728) and scope the tokens
   (`read:labs`, `read:programs`, `read:appointments`).
2. **Real data**: replace `sample_data.py` with authenticated Valeo API calls keyed to the token.
3. **Stable hosting**: any Python host works since there are no dependencies.
4. **A docs page** like Function Health's, and submit to the Connectors Directory.

## Limits worth knowing

| Constraint | Limit |
| --- | --- |
| Claude.ai / Desktop tool result size | ~150,000 characters |
| Claude.ai / Desktop tool timeout | 300 seconds |
| Transport | Streamable HTTP (legacy HTTP+SSE deprecated) |

Not medical advice; summary-level data only.
