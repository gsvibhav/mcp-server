# 🧠 MCP + Agent Framework for Azure & Entra ID Automation

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-teal?logo=fastapi)
![Azure](https://img.shields.io/badge/Azure-Entra%20ID-blue?logo=microsoftazure)
![Terraform](https://img.shields.io/badge/Terraform-Automation-623CE4?logo=terraform)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Live--Ready-success)

This project is a **Modular Control Plane (MCP)** and **Agent API** combo built with **FastAPI + Microsoft Graph**.  
It automates real-world admin tasks across **Azure AD (Entra ID)** — from sign-in diagnostics to privileged role automation — all **inside your tenant**, with **no data ever leaving your environment**.

---

## 🚀 What It Can Do

✅ **Ping Microsoft Graph**  
Confirm your app registration, token validity, and Graph connectivity.

✅ **Check user lockouts**  
Analyze sign-in failures, password resets, and Conditional Access impact.

✅ **Automate PIM assignments**  
Managers can request and approve time-boxed eligible roles through the Agent (Slack/Teams/Jira integration supported).

✅ **Add your own tools**  
Extend the MCP by simply adding new functions (Intune checks, compliance reviews, Sentinel queries, etc.).

---

## 🧩 How It Fits Together

```
User / AI Client ─▶ Agent API ─▶ MCP Server ─▶ Microsoft Graph ─▶ Entra ID / Azure AD
          │             │
   Slack / Jira   Local REST calls
```

Everything runs locally or in your tenant. No external dependencies, no hidden calls.

---

## 🛠 Prerequisites

| Requirement | Description |
|--------------|-------------|
| Python 3.10+ | Tested on 3.10.6 |
| Azure App Registration | Needs `RoleManagement.ReadWrite.Directory`, `Directory.Read.All`, and `AuditLog.Read.All` |
| OS | Works on Windows / Linux / macOS |
| (Optional) Jira & Slack/Teams | For approval flows |

---

## ⚙️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/<your-username>/mcp-agent.git
cd mcp-agent
```

### 2. Create a virtual environment
```bash
python -m venv .venv
.\.venv\Scripts\activate     # Windows
# source .venv/bin/activate    # Linux/macOS
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Create `.env` files

**MCP Server (.env):**
```bash
TENANT_ID=<your-tenant-id>
CLIENT_ID=<app-client-id>
CLIENT_SECRET=<client-secret>
PIM_SCOPE_ALLOWLIST=/
PIM_SIMULATE=false
```

**Agent (.env):**
```bash
MCP_BASE=http://127.0.0.1:8000
AGENT_API_KEY=supersecret
APPROVAL_SHARED_SECRET=topsecret
APPROVAL_CLICK_TOKEN=clicksecret
PUBLIC_BASE_URL=http://127.0.0.1:8001
```

---

## ▶️ Run the servers

**Terminal 1 – MCP Server**
```bash
uvicorn server:app --reload --port 8000
```

**Terminal 2 – Agent API**
```bash
uvicorn agent_api:app --reload --port 8001
```

Check health:
```bash
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8001/health
```

---

## 💡 Example: Check User Lockout

```bash
$body = @{ tool="entra_user_lockout"; input=@{ upn="alice@contoso.com" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/run -Body $body -ContentType "application/json"
```

Output:
```json
{
  "upn": "alice@contoso.com",
  "status": "ok_after_failures",
  "failure_count": 2,
  "success_count": 8,
  "recommendation": "No action needed. User signed in successfully later."
}
```

---

## 💬 Example: Request a PIM Role

```powershell
$headers = @{ Authorization = "Bearer supersecret" }
$ctx = @{
  role_name_or_id  = "Helpdesk Administrator"
  scope            = "/"
  duration_minutes = 120
  manager_upn      = "manager@contoso.com"
  justification    = "OPS-1432 temporary access"
}
$body = @{ message = "request pim for user@contoso.com"; context = $ctx } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri http://127.0.0.1:8001/agent -Method Post -Headers $headers -Body $body -ContentType "application/json"
```

The Agent:
1. Creates a Jira ticket.  
2. Sends approval links to Slack/Teams.  
3. Waits for manager approval.  
4. Calls MCP to perform the **real PIM assignment** through Microsoft Graph.  

---

## 🧩 Extending the Framework

Add a custom tool to `mcp_tools.py`:
```python
@log_tool
def my_custom_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"message": "Hello from your custom tool!"}
```
It will automatically appear under `/tools` and `/run`.

---

## 👨‍💻 Author

**Sai Vibhav Gudala**  
☁ Cloud & Identity Engineer | Azure • Entra ID • Terraform • Bicep • Security Automation  
Exploring how **AI Agents** can manage infrastructure, identity, and compliance — without human drag.

---

## 🪪 License

MIT License — use, modify, and improve freely.  
Just don’t commit credentials or secrets to GitHub.

---

## ⚡ Next Steps

- Add Intune device compliance checks  
- Enforce Conditional Access policies automatically  
- Run Sentinel queries via Graph  
- Expand PIM review automation  

> 🎯 Tip: This repo already works in **simulate mode** if you’re not ready for live Graph writes.
> Flip `PIM_SIMULATE=true` to safely explore before going live.
