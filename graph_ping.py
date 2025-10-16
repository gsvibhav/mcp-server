# --------------------------------------------
# graph_ping.py
# Goal: Prove your environment can authenticate to Microsoft Graph
# and return basic tenant info as clean JSON.
# --------------------------------------------

# 1) Standard libs we need
import os            # read environment variables (from .env)
import json          # print clean JSON the assistant/MCP can consume
import sys           # exit early with clear messages

# 2) Third-party libs
#    - python-dotenv: loads .env so secrets aren't hardcoded
#    - msal: official Microsoft auth library
#    - requests: simple HTTP client
from dotenv import load_dotenv
from msal import ConfidentialClientApplication
import requests

# 3) Load .env into environment variables
#    Why: keep secrets out of code and out of source control.
load_dotenv()

# 4) Read the three required values from your .env
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# 5) Fail fast if any secret is missing (clear error beats silent failure)
if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
    sys.exit("Missing TENANT_ID, CLIENT_ID, or CLIENT_SECRET in your .env file.")

# 6) Build MSAL configuration
#    - authority: which Entra tenant to authenticate against
#    - scope: '.default' means "use the app's configured API permissions"
authority = f"https://login.microsoftonline.com/{TENANT_ID}"
scopes = ["https://graph.microsoft.com/.default"]

# 7) Create the MSAL app (client credentials = server-to-server, no user MFA)
app = ConfidentialClientApplication(
    client_id=CLIENT_ID,
    client_credential=CLIENT_SECRET,
    authority=authority,
)

# 8) Get an access token
#    - try cache first (cheap), then real request
result = app.acquire_token_silent(scopes=scopes, account=None)
if not result:
    result = app.acquire_token_for_client(scopes=scopes)

# 9) If auth failed, show the reason and stop
if "access_token" not in result:
    # MSAL puts human-friendly info in error_description
    sys.exit(f"Auth failed: {result.get('error')} | {result.get('error_description')}")

access_token = result["access_token"]

# 10) Call Microsoft Graph
#     We query 'organization' to fetch tenant display name + id in one call.
#     Select only what we need to keep payload small.
url = "https://graph.microsoft.com/v1.0/organization?$select=displayName,id"
headers = {"Authorization": f"Bearer {access_token}"}

try:
    resp = requests.get(url, headers=headers, timeout=20)  # timeout avoids hanging
except requests.RequestException as e:
    sys.exit(f"Network error calling Graph: {e}")

# 11) Handle non-200 errors with the body included (useful for permission issues)
if resp.status_code != 200:
    sys.exit(f"Graph error {resp.status_code}: {resp.text}")

# 12) Parse JSON safely and build a tiny, assistant-friendly summary
payload = resp.json()
org = payload.get("value", [{}])[0]
summary = {
    "tenant_display_name": org.get("displayName"),
    "tenant_id": org.get("id"),
    "ok": True
}

# 13) Print pretty JSON to stdout (MCP/assistants love this shape)
print(json.dumps(summary, indent=2))
