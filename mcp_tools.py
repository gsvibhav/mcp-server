# mcp_tools.py
# Minimal MCP-style tool registry:
# - graph_ping
# - entra_user_lockout
# - pim_assign (eligible)  -> now supports simulate=True (no Graph calls)
# - pim_configure_role     -> configure approver + MFA + justification + ticket + max duration

from typing import Dict, Any, Callable, List, Optional
import os, time, requests, datetime as dt, re
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

# -------- PIM eligible assign (safe, approval-driven) --------
class PIMAssignInput(BaseModel):
    principal_upn: str = Field(..., description="User UPN to receive eligibility")
    role_name_or_id: Optional[str] = Field(None, description="Role display name (e.g., 'Helpdesk Administrator') or roleDefinitionId GUID")
    role_id: Optional[str] = Field(None, description="Alias for role_name_or_id if your client sends role_id")
    scope: str = Field("/", description="Directory scope id. For tenant root use '/'")
    duration_minutes: int = Field(60, ge=1, description="Requested eligibility window in minutes")
    justification: str = Field(..., description="Why this is needed (include ticket ID)")
    dry_run: bool = Field(True, description="If true, do not call Graph; just return the plan")
    require_ticket: bool = Field(True, description="If true, justification must include a ticket key/reference")
    simulate: bool = Field(False, description="If true, skip all Graph calls and fabricate IDs for a dry-run")

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

def _allowed_scope(scope: str) -> bool:
    allow_raw = os.getenv("PIM_SCOPE_ALLOWLIST", "/")
    allow = [s.strip() for s in allow_raw.split(",") if s.strip()]
    return scope in allow

def _get_user_id(upn: str, token: str) -> str:
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{upn}?$select=id",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20
    )
    if r.status_code != 200:
        raise RuntimeError(f"User lookup failed for {upn}: {r.status_code} {r.text}")
    return r.json().get("id")

def _resolve_role_def_id(role_name_or_id: str, token: str) -> str:
    # If it looks like a GUID, just return it
    if re.fullmatch(r"[0-9a-fA-F-]{36}", role_name_or_id):
        return role_name_or_id
    # Else look up by display name
    filt = requests.utils.quote(f"displayName eq '{role_name_or_id}'", safe="=")
    url = f"https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions?$filter={filt}&$select=id,displayName"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Role definition lookup failed: {r.status_code} {r.text}")
    items = r.json().get("value", [])
    if not items:
        raise RuntimeError(f"No role definition found for displayName='{role_name_or_id}'")
    return items[0]["id"]

@log_tool
def pim_assign_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    # 1) Validate input
    try:
        args = PIMAssignInput(**payload)
    except ValidationError as ve:
        raise RuntimeError(f"Invalid input: {ve.errors()}")

    # Normalize role field (support role_id alias)
    role_name_or_id = args.role_name_or_id or args.role_id
    if not role_name_or_id:
        raise RuntimeError("Provide 'role_name_or_id' (display name or GUID) or 'role_id'")

    # 2) Guardrails
    min_d = _env_int("PIM_MIN_DURATION", 15)
    max_d = _env_int("PIM_MAX_DURATION", 240)
    if not (min_d <= args.duration_minutes <= max_d):
        raise RuntimeError(f"Duration must be between {min_d} and {max_d} minutes")

    if not _allowed_scope(args.scope):
        raise RuntimeError(f"Scope '{args.scope}' not allowed. Update PIM_SCOPE_ALLOWLIST in .env")

    if args.require_ticket and not any(x in (args.justification or "").lower() for x in ["ops-", "sec-", "iac-", "ticket", "inc", "chg"]):
        raise RuntimeError("Justification must include a ticket reference (e.g., OPS-1234) when require_ticket=true")

    # 2.5) Simulate mode: no Graph calls at all — return a fabricated plan
    if args.simulate:
        duration_iso = f"PT{int(args.duration_minutes)}M"
        fake_principal_id = "00000000-0000-0000-0000-FAKEUSERID0001"
        fake_role_def_id  = "00000000-0000-0000-0000-FAKEROLEID0001"
        body = {
            "action": "adminAssign",
            "justification": args.justification,
            "principalId": fake_principal_id,
            "roleDefinitionId": fake_role_def_id,
            "directoryScopeId": args.scope,
            "scheduleInfo": {
                "startDateTime": None,
                "expiration": {"type": "afterDuration", "duration": duration_iso}
            }
        }
        return {
            "status": "dry_run_simulated",
            "plan": {
                "endpoint": "/roleManagement/directory/roleEligibilityScheduleRequests",
                "body": body
            },
            "guardrails": {
                "scope_ok": True,
                "duration_ok": True,
                "ticket_ok": not args.require_ticket or True
            },
            "resolved": {
                "principal_id": fake_principal_id,
                "role_definition_id": fake_role_def_id
            }
        }

    # 3) Resolve Graph identifiers (real mode)
    token = get_graph_token()
    principal_id = _get_user_id(args.principal_upn, token)
    role_def_id = _resolve_role_def_id(role_name_or_id, token)

    # 4) Build eligible assignment request body
    duration_iso = f"PT{int(args.duration_minutes)}M"
    body = {
        "action": "adminAssign",
        "justification": args.justification,
        "principalId": principal_id,
        "roleDefinitionId": role_def_id,
        "directoryScopeId": args.scope,  # "/" for tenant
        "scheduleInfo": {
            "startDateTime": None,
            "expiration": {"type": "afterDuration", "duration": duration_iso}
        }
    }

    # 5) Dry-run flag (does not call Graph, but uses real looked-up IDs)
    if args.dry_run:
        return {
            "status": "dry_run",
            "plan": {
                "endpoint": "/roleManagement/directory/roleEligibilityScheduleRequests",
                "body": body
            },
            "guardrails": {
                "scope_ok": True,
                "duration_ok": True,
                "ticket_ok": not args.require_ticket or True
            },
            "resolved": {
                "principal_id": principal_id,
                "role_definition_id": role_def_id
            }
        }

    # 6) Execute (real call)
    url = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleEligibilityScheduleRequests"
    r = requests.post(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }, json=body, timeout=30)

    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"PIM assign failed: {r.status_code} {r.text}")

    res = r.json()
    return {
        "status": "eligible_created",
        "request_id": res.get("id"),
        "role_definition_id": role_def_id,
        "principal_id": principal_id,
        "scope": args.scope,
        "duration_minutes": args.duration_minutes
    }

# -------- PIM configure role (approver + MFA + justification + ticket + max duration) --------
class PIMConfigInput(BaseModel):
    role_name_or_id: str = Field(..., description="Role display name (e.g., 'Helpdesk Administrator') or GUID")
    manager_upn: str = Field(..., description="Manager who must approve activations")
    scope: str = Field("/", description="Policy scope. Use '/' for tenant-wide directory roles")
    max_minutes: int = Field(120, ge=15, le=480, description="Activation max duration in minutes")
    dry_run: bool = Field(True, description="Preview only when true (default)")

def _get_user_object_id(upn: str, token: str) -> str:
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{upn}?$select=id",
        headers={"Authorization": f"Bearer {token}"}, timeout=20
    )
    if r.status_code != 200:
        raise RuntimeError(f"Manager lookup failed for {upn}: {r.status_code} {r.text}")
    return r.json()["id"]

def _get_role_def_id_by_name_or_id(label: str, token: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F-]{36}", label):
        return label
    q = requests.utils.quote(f"displayName eq '{label}'", safe="=")
    url = f"https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions?$filter={q}&$select=id,displayName"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Role definition lookup failed: {r.status_code} {r.text}")
    items = r.json().get("value", [])
    if not items:
        raise RuntimeError(f"No role definition found for displayName='{label}'")
    return items[0]["id"]

def _get_policy_assignment_for_role(role_def_id: str, scope: str, token: str) -> dict:
    filt = requests.utils.quote(
        f"scopeId eq '{scope}' and scopeType eq 'Directory' and roleDefinitionId eq '{role_def_id}'",
        safe="= '"
    )
    url = f"https://graph.microsoft.com/v1.0/policies/roleManagementPolicyAssignments?$filter={filt}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Policy assignment lookup failed: {r.status_code} {r.text}")
    vals = r.json().get("value", [])
    if not vals:
        raise RuntimeError("No roleManagementPolicyAssignment found for this role at the given scope")
    return vals[0]  # contains policyId

def _patch_rule(policy_id: str, rule_id: str, body: dict, token: str) -> dict:
    url = f"https://graph.microsoft.com/v1.0/policies/roleManagementPolicies/{policy_id}/rules/{rule_id}"
    r = requests.patch(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                       json=body, timeout=30)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Failed to update rule {rule_id}: {r.status_code} {r.text}")
    return {"rule": rule_id, "status": "updated"}

@log_tool
def pim_configure_role_handler(payload: Dict[str, Any]) -> Dict[str, Any]:
    # 1) Validate
    try:
        args = PIMConfigInput(**payload)
    except ValidationError as ve:
        raise RuntimeError(f"Invalid input: {ve.errors()}")

    if not (15 <= args.max_minutes <= 480):
        raise RuntimeError("max_minutes must be between 15 and 480")

    token = get_graph_token()
    role_def_id = _get_role_def_id_by_name_or_id(args.role_name_or_id, token)
    manager_id = _get_user_object_id(args.manager_upn, token)
    assignment = _get_policy_assignment_for_role(role_def_id, args.scope, token)
    policy_id = assignment.get("policyId")

    approval_rule_id = "Approval_EndUser_Assignment"
    approval_body = {
        "id": approval_rule_id,
        "setting": {
            "isApprovalRequired": True,
            "stages": [
                {
                    "approvalStageTimeOutInDays": 1,
                    "isApproverJustificationRequired": True,
                    "primaryApprovers": [
                        {"userId": manager_id}
                    ],
                    "escalationTimeInMinutes": 0
                }
            ]
        }
    }

    enablement_rule_id = "Enablement_EndUser_Assignment"
    enablement_body = {
        "id": enablement_rule_id,
        "enabledRules": ["Mfa", "Justification", "Ticketing"]
    }

    expiration_rule_id = "Expiration_EndUser_Assignment"
    expiration_body = {
        "id": expiration_rule_id,
        "isExpirationRequired": True,
        "maximumDuration": f"PT{int(args.max_minutes)}M"
    }

    if args.dry_run:
        return {
            "status": "dry_run",
            "policy_id": policy_id,
            "role_definition_id": role_def_id,
            "plan": [
                {"rule": approval_rule_id, "patch": approval_body},
                {"rule": enablement_rule_id, "patch": enablement_body},
                {"rule": expiration_rule_id, "patch": expiration_body},
            ]
        }

    results = []
    results.append(_patch_rule(policy_id, approval_rule_id, approval_body, token))
    results.append(_patch_rule(policy_id, enablement_rule_id, enablement_body, token))
    results.append(_patch_rule(policy_id, expiration_rule_id, expiration_body, token))

    return {
        "status": "configured",
        "role_definition_id": role_def_id,
        "policy_id": policy_id,
        "approver": args.manager_upn,
        "mfa_required": True,
        "justification_required": True,
        "ticket_required": True,
        "max_minutes": args.max_minutes,
        "rules_updated": results
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
    "pim_assign": Tool(
        name="pim_assign",
        description="Create an ELIGIBLE PIM assignment for a user at a given scope with time-boxed duration.",
        input_schema={
            "type": "object",
            "properties": {
                "principal_upn": {"type": "string"},
                "role_name_or_id": {"type": "string"},
                "role_id": {"type": "string"},
                "scope": {"type": "string"},
                "duration_minutes": {"type": "integer", "minimum": 1},
                "justification": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "require_ticket": {"type": "boolean"},
                "simulate": {"type": "boolean"}
            },
            "required": ["principal_upn", "scope", "duration_minutes", "justification"]
        },
        handler=pim_assign_handler,
    ),
    "pim_configure_role": Tool(
        name="pim_configure_role",
        description="Configure a role's PIM policy: manager as approver, require MFA, justification, ticket, and set activation max duration.",
        input_schema={
            "type": "object",
            "properties": {
                "role_name_or_id": {"type": "string"},
                "manager_upn": {"type": "string"},
                "scope": {"type": "string"},
                "max_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
                "dry_run": {"type": "boolean"}
            },
            "required": ["role_name_or_id", "manager_upn"]
        },
        handler=pim_configure_role_handler,
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
