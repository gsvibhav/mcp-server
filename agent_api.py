# agent_api.py
# Agent API: PIM requests + approvals (Jira + Slack/Teams), lockout helper, tenant ping.
# Live-ready: env toggle for simulate vs live, strict auth, clearer errors.

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from typing import Dict, Any, Optional
import os, re, requests, json, time, uuid
from dotenv import load_dotenv

from adapters.jira import create_issue, comment
from adapters.notify import send_slack_approval, send_teams_approval, build_approval_links

load_dotenv()

app = FastAPI(title="Agent API", version="0.7")

# ---------- config ----------
MCP_BASE = os.getenv("MCP_BASE", "http://127.0.0.1:8000")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "dev")
APPROVAL_SHARED_SECRET = os.getenv("APPROVAL_SHARED_SECRET", "devsecret")
APPROVAL_CLICK_TOKEN = os.getenv("APPROVAL_CLICK_TOKEN", "clicksecret")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8001")
DEFAULT_SIMULATE = os.getenv("PIM_SIMULATE", "false").lower() == "true"  # live by default
PENDING_TTL_SEC = int(os.getenv("PENDING_TTL_SEC", "1800"))  # 30 min default

# Simple in-memory store
PENDING: Dict[str, Dict[str, Any]] = {}

# ---------- models ----------
class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None

class AgentResponse(BaseModel):
    reply: str
    data: Optional[Dict[str, Any]] = None
    request_id: str

class ApprovalBody(BaseModel):
    request_id: str = Field(..., description="Request ID returned by /agent")
    ticket: str = Field(..., description="Jira ticket key (or MOCK-xxx)")
    approved: bool = Field(..., description="Manager decision")
    approver_upn: str = Field(..., description="Manager UPN recorded in request")

# ---------- helpers ----------
def _auth_or_401(token: Optional[str]):
    if token != f"Bearer {AGENT_API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

def _approval_or_401(token: Optional[str]):
    if token != f"Bearer {APPROVAL_SHARED_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized approval webhook")

def list_tools() -> Dict[str, Dict[str, Any]]:
    r = requests.get(f"{MCP_BASE}/tools", timeout=10)
    r.raise_for_status()
    return {t["name"]: t for t in r.json()}

def run_tool(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{MCP_BASE}/run", json={"tool": name, "input": payload}, timeout=120)
    r.raise_for_status()
    body = r.json()
    # server returns {"result": {...}} per our MCP server
    return body.get("result", body)

def extract_upn(text: str) -> Optional[str]:
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return m.group(0) if m else None

def _now_ms() -> int:
    return int(time.time() * 1000)

def _cleanup_pending():
    # drop expired requests
    now = time.time()
    remove = []
    for k, v in PENDING.items():
        ts = v.get("_ts", now)
        if now - ts > PENDING_TTL_SEC:
            remove.append(k)
    for k in remove:
        PENDING.pop(k, None)

# ---------- routes ----------
@app.get("/health")
def health():
    # Surface MCP reachability + tools (helps triage)
    try:
        tools = list_tools()
        return {"ok": True, "mcp_ok": True, "tools": list(tools.keys())}
    except Exception as e:
        return {"ok": True, "mcp_ok": False, "error": str(e)}

@app.post("/agent", response_model=AgentResponse)
def agent_chat(payload: ChatRequest, authorization: Optional[str] = Header(None)):
    _auth_or_401(authorization)
    _cleanup_pending()

    request_id = f"req_{_now_ms()}_{uuid.uuid4().hex[:6]}"
    text_raw = (payload.message or "").strip()
    text = text_raw.lower()
    try:
        tools = list_tools()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach MCP server: {e}")

    # 1) Lockout helper
    if any(k in text for k in [" lockout", "sign in", "signin", "login", "auth "]) or text.startswith("lockout"):
        upn = extract_upn(text_raw)
        if not upn:
            return AgentResponse(
                reply="Need a user UPN. Try: check lockout for alice@contoso.com",
                request_id=request_id
            )
        if "entra_user_lockout" not in tools:
            return AgentResponse(reply="Lockout tool not available on MCP.", request_id=request_id)
        try:
            result = run_tool("entra_user_lockout", {"upn": upn, "lookback_hours": 24, "interactive_only": True})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Lockout check failed: {e}")
        summary = f"Sign-in status for {upn}: {result.get('status')}. Failures={result.get('failure_count')} Successes={result.get('success_count')}."
        return AgentResponse(reply=summary, data=result, request_id=request_id)

    # 2) Tenant ping
    if "tenant" in text or "ping" in text:
        if "graph_ping" not in tools:
            return AgentResponse(reply="Graph ping tool not available on MCP.", request_id=request_id)
        try:
            result = run_tool("graph_ping", {})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Graph ping failed: {e}")
        summary = f"Tenant: {result.get('tenant_display_name')} ({result.get('tenant_id')})."
        return AgentResponse(reply=summary, data=result, request_id=request_id)

    # 3) Manager raises a PIM request via chat → open Jira ticket → notify Slack/Teams → await approval
    if "pim" in text and any(k in text for k in ["request", "assign", "create"]):
        if "pim_assign" not in tools:
            return AgentResponse(reply="PIM assign tool not available on MCP.", request_id=request_id)

        ctx = payload.context or {}
        upn = extract_upn(text_raw)
        role_label = ctx.get("role_name_or_id") or ctx.get("role_id")
        duration_minutes = int(ctx.get("duration_minutes", 120))
        scope = ctx.get("scope", "/")
        manager_upn = ctx.get("manager_upn")
        justification = ctx.get("justification") or "PIM eligibility requested by manager"
        simulate = bool(ctx.get("simulate", DEFAULT_SIMULATE))  # env default, override per request

        # Validate inputs
        missing = []
        if not upn: missing.append("user upn in message")
        if not role_label: missing.append("role_name_or_id (or role_id)")
        if not manager_upn: missing.append("manager_upn")
        if missing:
            example = {
                "role_name_or_id": "Helpdesk Administrator",
                "scope": "/",
                "duration_minutes": 120,
                "manager_upn": "manager@contoso.com",
                "justification": "OPS-1432 temp access",
                "simulate": True
            }
            return AgentResponse(
                reply=("Missing: " + ", ".join(missing) +
                       ". Include role_name_or_id (or role_id), scope, duration_minutes, "
                       f"manager_upn, justification in context. Example: {json.dumps(example)}"),
                request_id=request_id
            )

        # Open Jira ticket (mock or real)
        summary = f"[PIM Request] {upn} → {role_label} for {duration_minutes}m (Scope {scope})"
        desc = (
            f"*Manager*: {manager_upn}\n"
            f"*User*: {upn}\n"
            f"*Requested Role*: {role_label}\n"
            f"*Scope*: {scope}\n"
            f"*Duration*: {duration_minutes} minutes\n"
            f"*Justification*: {justification}\n\n"
            "Plan:\n"
            "1) On approval, create ELIGIBLE PIM assignment (time-boxed)\n"
            "2) Comment assignment result back to this ticket\n"
            "3) Activation follows PIM role settings (MFA/approval/ticket enforced)"
        )
        try:
            issue = create_issue(summary, desc, issue_type="Task", labels=["PIM", "IDENTITY", "APPROVAL_REQUIRED"])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to create ticket: {e}")
        ticket_key = issue.get("key")

        # Save pending request
        PENDING[request_id] = {
            "_ts": time.time(),
            "type": "pim_assign",
            "ticket": ticket_key,
            "manager_upn": manager_upn,
            "inputs": {
                "principal_upn": upn,
                "role_name_or_id": role_label,
                "scope": scope,
                "duration_minutes": duration_minutes,
                "justification": f"{ticket_key}: {justification}",
                "dry_run": False,       # live flow (MCP tool has guardrails)
                "simulate": simulate,   # True => MCP will fabricate IDs, no Graph
                "require_ticket": True
            }
        }

        # Slack/Teams approval buttons (optional)
        title = f"PIM approval needed for {upn}"
        details = f"Role: {role_label}\nScope: {scope}\nDuration: {duration_minutes}m\nTicket: {ticket_key}\nManager: {manager_upn}"
        slack_res = send_slack_approval(request_id, ticket_key, title, details, manager_upn)
        teams_res = send_teams_approval(request_id, ticket_key, title, details, manager_upn)

        # Include clickable URLs in API response
        approve_url, deny_url = build_approval_links(request_id, ticket_key, manager_upn)

        msg = (f"PIM ticket {ticket_key} created. Waiting for manager approval. "
               f"Use Approve/Deny buttons in Slack/Teams if configured, or call /approvals/pim.")
        return AgentResponse(
            reply=msg,
            data={
                "ticket": ticket_key,
                "request_id": request_id,
                "approval_links": {"approve": approve_url, "deny": deny_url},
                "notify": {"slack": slack_res, "teams": teams_res}
            },
            request_id=request_id
        )

    # Fallback help
    help_text = ("Try: 'check lockout for user@contoso.com', 'ping tenant', or "
                 "'request pim for user@contoso.com' with context "
                 "{role_name_or_id, scope, duration_minutes, manager_upn, justification, simulate}.")
    return AgentResponse(reply=help_text, request_id=request_id)

# ---------- approval webhook (programmatic) ----------
@app.post("/approvals/pim")
def approvals_pim(body: ApprovalBody, authorization: Optional[str] = Header(None)):
    _approval_or_401(authorization)
    _cleanup_pending()

    rec = PENDING.get(body.request_id)
    if not rec or rec.get("type") != "pim_assign":
        raise HTTPException(status_code=404, detail="Request not found or type mismatch")
    if rec.get("ticket") != body.ticket:
        raise HTTPException(status_code=400, detail="Ticket mismatch")

    expected_mgr = rec.get("manager_upn", "").lower()
    if body.approved and body.approver_upn.lower() != expected_mgr:
        raise HTTPException(status_code=403, detail="Only the recorded manager can approve this request")

    ticket_key = rec["ticket"]

    # Deny path
    if not body.approved:
        try:
            comment(ticket_key, f"Manager {body.approver_upn} denied approval. Request {body.request_id} closed.")
        except Exception:
            pass
        PENDING.pop(body.request_id, None)
        return {"status": "denied"}

    # Approved path
    try:
        result = run_tool("pim_assign", rec["inputs"])
        try:
            comment(ticket_key, f"Approved by {body.approver_upn}. PIM eligible assignment created.\n\nResult:\n{json.dumps(result, indent=2)}")
        except Exception:
            pass
        PENDING.pop(body.request_id, None)
        return {"status": "eligible_created", "result": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"Error creating PIM assignment: {e}"
        try:
            comment(ticket_key, msg)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=msg)

# ---------- approval via clickable link (Slack/Teams buttons) ----------
@app.get("/approvals/pim/click")
def approvals_pim_click(request: Request, token: str, request_id: str, ticket: str, decision: str, approver_upn: str):
    if token != APPROVAL_CLICK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized click token")
    _cleanup_pending()

    rec = PENDING.get(request_id)
    if not rec or rec.get("type") != "pim_assign":
        raise HTTPException(status_code=404, detail="Request not found or type mismatch")
    if rec.get("ticket") != ticket:
        raise HTTPException(status_code=400, detail="Ticket mismatch")
    if approver_upn.lower() != rec.get("manager_upn", "").lower():
        raise HTTPException(status_code=403, detail="Only the recorded manager can approve/deny this request")

    if decision == "deny":
        try:
            comment(ticket, f"Manager {approver_upn} denied approval. Request {request_id} closed.")
        except Exception:
            pass
        PENDING.pop(request_id, None)
        return {"status": "denied"}

    if decision != "approve":
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'deny'")

    try:
        result = run_tool("pim_assign", rec["inputs"])
        try:
            comment(ticket, f"Approved by {approver_upn}. PIM eligible assignment created.\n\nResult:\n{json.dumps(result, indent=2)}")
        except Exception:
            pass
        PENDING.pop(request_id, None)
        return {"status": "eligible_created", "result": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"Error creating PIM assignment: {e}"
        try:
            comment(ticket, msg)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=msg)
