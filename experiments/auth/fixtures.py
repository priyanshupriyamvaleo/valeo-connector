"""Fixtures for the auth experiment. Every value here is invented.

These accounts exist only on localhost and guard nothing real. The test client reads the
credentials from this file so they never have to be typed or pasted anywhere.

Two members, with deliberately different results. That is the point of the experiment: a
test with one member passes even when authentication is completely broken and everybody is
being handed the same record.
"""

MEMBERS = {
    "sundeep": {
        "password": "sundeep-local-test-8812",
        "member_id": "mem_4821",
        "first_name": "Sundeep",
        "city": "Dubai",
        "lab_summary": {
            "panel": "Valeo Complete Blood Panel",
            "collected_on": "2026-07-28",
            "total": 68,
            "in_range": 59,
            "out_of_range": 9,
            "flagged": [
                {"marker": "Vitamin D", "direction": "low", "severity": "significant"},
                {"marker": "LDL Cholesterol", "direction": "high", "severity": "moderate"},
                {"marker": "Ferritin", "direction": "low", "severity": "moderate"},
            ],
        },
    },
    "meera": {
        "password": "meera-local-test-5530",
        "member_id": "mem_7390",
        "first_name": "Meera",
        "city": "Abu Dhabi",
        "lab_summary": {
            "panel": "Valeo Essential Panel",
            "collected_on": "2026-08-11",
            "total": 42,
            "in_range": 40,
            "out_of_range": 2,
            "flagged": [
                {"marker": "TSH", "direction": "high", "severity": "mild"},
                {"marker": "Vitamin B12", "direction": "low", "severity": "mild"},
            ],
        },
    },
}

# member_id -> username, so the API can resolve a token's subject back to a member.
BY_MEMBER_ID = {m["member_id"]: username for username, m in MEMBERS.items()}

API_PORT = 9001
AUTH_PORT = 9002
MCP_PORT = 8788
CALLBACK_PORT = 9100

API_URL = "http://127.0.0.1:%d" % API_PORT
AUTH_URL = "http://127.0.0.1:%d" % AUTH_PORT
MCP_URL = "http://127.0.0.1:%d" % MCP_PORT
MCP_RESOURCE = MCP_URL + "/mcp"
REDIRECT_URI = "http://127.0.0.1:%d/callback" % CALLBACK_PORT

SCOPES = ["read:labs", "read:profile", "offline_access"]

# Short on purpose so the experiment can show an expired token being refreshed.
ACCESS_TOKEN_TTL = 300
