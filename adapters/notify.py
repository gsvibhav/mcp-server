# adapters/notify.py
import os, requests
from urllib.parse import urlencode

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8001")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")
APPROVAL_CLICK_TOKEN = os.getenv("APPROVAL_CLICK_TOKEN", "clicksecret")

def build_approval_links(request_id: str, ticket: str, approver_upn: str):
    base = f"{PUBLIC_BASE_URL}/approvals/pim/click"
    approve_qs = urlencode({
        "token": APPROVAL_CLICK_TOKEN,
        "request_id": request_id,
        "ticket": ticket,
        "decision": "approve",
        "approver_upn": approver_upn
    })
    deny_qs = urlencode({
        "token": APPROVAL_CLICK_TOKEN,
        "request_id": request_id,
        "ticket": ticket,
        "decision": "deny",
        "approver_upn": approver_upn
    })
    return f"{base}?{approve_qs}", f"{base}?{deny_qs}"

def send_slack_approval(request_id: str, ticket: str, title: str, details: str, approver_upn: str):
    if not SLACK_WEBHOOK_URL:
        return {"sent": False, "reason": "SLACK_WEBHOOK_URL not set"}
    approve_url, deny_url = build_approval_links(request_id, ticket, approver_upn)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{details}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve ✅"}, "url": approve_url},
            {"type": "button", "text": {"type": "plain_text", "text": "Deny ❌"}, "url": deny_url}
        ]}
    ]
    r = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=15)
    return {"sent": r.status_code in (200, 204), "status": r.status_code, "text": getattr(r, "text", "")}

def send_teams_approval(request_id: str, ticket: str, title: str, details: str, approver_upn: str):
    if not TEAMS_WEBHOOK_URL:
        return {"sent": False, "reason": "TEAMS_WEBHOOK_URL not set"}
    approve_url, deny_url = build_approval_links(request_id, ticket, approver_upn)
    # Simple MessageCard with two action buttons
    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": title,
        "themeColor": "0078D7",
        "title": title,
        "text": details,
        "potentialAction": [
            {"@type": "OpenUri", "name": "Approve ✅", "targets": [{"os": "default", "uri": approve_url}]},
            {"@type": "OpenUri", "name": "Deny ❌", "targets": [{"os": "default", "uri": deny_url}]}
        ]
    }
    r = requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=15)
    return {"sent": r.status_code in (200, 204), "status": r.status_code, "text": getattr(r, "text", "")}
