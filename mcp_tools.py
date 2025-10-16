# mcp_tools.py
# Minimal MCP-style tool registry: graph_ping + entra_user_lockout

from typing import Dict, Any, Callable, List
import os, time, requests, datetime as dt
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv
from msal import ConfidentialClientApplication
from collections import Counter

# ---------- minimal logger ----------
def log_tool(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
    def wrapper(payload: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.time()
        name = getattr(fn, "__name__", "tool")
        try:
            result = fn(payload)
            dur = int((time.time() - t0) * 1000)
            print(f"[TOOL] name={name} status=ok duration_ms={dur}")
            return result
        except Exception as e:
            dur = int((time.time() - t0) * 1000)
            print(f"[TOOL] name={name} status=error duration_ms={dur} error={e}")
            raise
    return wrapper

# ---------- shared auth ----------
def get_graph_token() -> str:
    load_dotenv()
    tenant = os.getenv("TENANT_ID")
    client = os.getenv("CLIENT_ID")
    secret = os.getenv("CLIENT_SECRET")
    if not all([tenant, client, secret]):
        raise RuntimeError("Missing TENANT_ID/CLIENT_ID/CLIENT_SECRET in .env")

    authority = f"https://login.microsoftonline.com/{tenant}"
    scopes = ["https://graph.microsoft.com/.default"]
    app = ConfidentialClientApplication(
        client_id=client, client_credential=secret, authority=authority
    )
    result = app.acquire_token_silent(scopes=scopes, account=None) or \
             app.acquire_token_for_client(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error')} | {result.get('error_description')}")
    return result["access_token"]

# ---------- tool contracts ----------
class Tool(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]

# -------- graph_ping (no input) --------
class GraphPingInput(BaseModel):
    pass

@log_tool
def graph_ping_handler(_: Dict[str, Any]) -> Dict[str, Any]:
    token = get_graph_token()
    r = requests.get(
        "https://graph.microsoft.com/v1.0/organization?$select=displayName,id",
        headers={"Authorization": f"Bearer {token}"}, timeout=20
    )
    if r.status_code != 200:
        raise RuntimeError(f"Graph error {r.status_code}: {r.text}")
    org = r.json().get("value", [{}])[0]
    return {
        "tenant_display_name": org.get("displayName"),
        "tenant_id": org.get("id"),
        "ok": True
    }

# -------- entra_user_lockout --------
class LockoutInput(BaseModel):
    upn: str = Field(..., description="User principal name, e.g. alice@contoso.com")
    lookback_hours: int = Field(24, ge=1, le=168, description="Hours to look back (1–168)")
    interactive_only: bool = Field(True, description="Only include interactive user sign-ins")

@log_tool
def entra_user_lockout_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    # 1) Validate inputs
    try:
        args = LockoutInput(**payload)
    except ValidationError as ve:
        raise RuntimeError(f"Invalid input: {ve.errors()}")

    token = get_graph_token()

    # 2) Query sign-in logs
    since = (dt.datetime.utcnow() - dt.timedelta(hours=args.lookback_hours)).replace(microsecond=0).isoformat() + "Z"
    select = ",".join([
        "id","createdDateTime","userPrincipalName","appDisplayName","status",
        "isInteractive","conditionalAccessStatus","appliedConditionalAccessPolicies"
    ])
    base_filter = f"userPrincipalName eq '{args.upn}' and createdDateTime ge {since}"
    if args.interactive_only:
        base_filter += " and isInteractive eq true"

    url = (
        "https://graph.microsoft.com/v1.0/auditLogs/signIns"
        f"?$filter={base_filter}&$select={select}&$top=50"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Graph error {r.status_code}: {r.text}")
    events = r.json().get("value", [])

    # 3) Summaries
    failures: List[int] = []
    last_failure_time = None
    last_success_time = None
    apps_success, apps_failure, ca_status = Counter(), Counter(), Counter()
    policies_hit: List[str] = []

    for e in events:
        st = e.get("status") or {}
        code = st.get("errorCode")
        app = e.get("appDisplayName") or "Unknown"
        ca = e.get("conditionalAccessStatus") or "none"
        if ca != "none":
            ca_status[ca] += 1

        ts = e.get("createdDateTime")
        if code and code != 0:
            failures.append(code)
            apps_failure[app] += 1
            if (last_failure_time is None) or (ts and ts > last_failure_time):
                last_failure_time = ts
        else:
            apps_success[app] += 1
            if (last_success_time is None) or (ts and ts > last_success_time):
                last_success_time = ts

        for p in (e.get("appliedConditionalAccessPolicies") or []):
            name = p.get("displayName")
            if name:
                policies_hit.append(name)

    success_count = sum(apps_success.values())
    failure_count = sum(apps_failure.values())
    top_errors = [{"code": c, "count": n} for c, n in Counter(failures).most_common(5)]
    policies_hit = sorted(list(set(policies_hit)))[:10]

    # 4) Status logic
    if failure_count == 0 and success_count > 0:
        status = "ok"
    elif success_count > 0 and failure_count > 0:
        if last_success_time and (not last_failure_time or last_success_time > last_failure_time):
            status = "ok_after_failures"
        else:
            status = "mixed_success"
    else:
        status = "blocked"

    return {
        "upn": args.upn,
        "lookback_hours": args.lookback_hours,
        "interactive_only": args.interactive_only,
        "status": status,
        "last_failure_time": last_failure_time,
        "last_success_time": last_success_time,
        "success_count": success_count,
        "failure_count": failure_count,
        "apps_success_top": apps_success.most_common(5),
        "apps_failure_top": apps_failure.most_common(5),
        "conditional_access_status": ca_status.most_common(),   # [] when none
        "top_errors": top_errors,
        "policies_hit": policies_hit,
        "recommendation": (
            "No action. Recent successes observed."
            if status in ("ok", "ok_after_failures")
            else "Review app assignment/licensing or device/risk posture; see top_errors/apps_failure_top."
        ),
    }

# ---------- registry ----------
TOOLS: Dict[str, Tool] = {
    "graph_ping": Tool(
        name="graph_ping",
        description="Return tenant display name and ID from Microsoft Graph.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=graph_ping_handler,
    ),
    "entra_user_lockout": Tool(
        name="entra_user_lockout",
        description="Summarize recent sign-in issues for a user (last N hours).",
        input_schema={
            "type": "object",
            "properties": {
                "upn": {"type": "string"},
                "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 168},
                "interactive_only": {"type": "boolean"},
            },
            "required": ["upn"],
        },
        handler=entra_user_lockout_handler,
    ),
}

def list_tools():
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in TOOLS.values()
    ]

def run_tool(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOLS[name].handler(payload)
