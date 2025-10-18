# adapters/jira.py
# Safe mockable Jira adapter for local testing

import os, requests, uuid

JIRA_BASE = os.getenv("JIRA_BASE")
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT", "OPS")
JIRA_IT_ASSIGNEE_ID = os.getenv("JIRA_IT_ASSIGNEE_ID")
MOCK_MODE = os.getenv("JIRA_MOCK", "true").lower() == "true"

def _auth():
    if MOCK_MODE:
        return None
    if not all([JIRA_BASE, JIRA_USER, JIRA_TOKEN]):
        raise RuntimeError("Missing JIRA_BASE, JIRA_USER, or JIRA_TOKEN in environment.")
    return (JIRA_USER, JIRA_TOKEN)

def create_issue(summary: str, description: str, issue_type="Task", labels=None):
    if MOCK_MODE:
        issue_id = f"MOCK-{str(uuid.uuid4())[:8]}"
        print(f"[MOCK] Created issue {issue_id} — {summary}")
        return {"key": issue_id, "id": issue_id, "mock": True}

    url = f"{JIRA_BASE}/rest/api/3/issue"
    fields = {
        "project": {"key": JIRA_PROJECT},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "description": description
    }
    if labels:
        fields["labels"] = labels
    if JIRA_IT_ASSIGNEE_ID:
        fields["assignee"] = {"id": JIRA_IT_ASSIGNEE_ID}

    r = requests.post(url, json={"fields": fields}, auth=_auth(), timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Jira issue creation failed: {r.status_code} {r.text}")
    return r.json()

def comment(issue_key: str, body: str):
    if MOCK_MODE:
        print(f"[MOCK] Added comment to {issue_key}: {body[:60]}...")
        return {"mock": True, "key": issue_key}
    url = f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/comment"
    r = requests.post(url, json={"body": body}, auth=_auth(), timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Jira comment failed: {r.status_code} {r.text}")
    return r.json()
