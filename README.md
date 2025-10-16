# 🧠 MCP Server - Azure & Entra ID Automation Framework

This project is a **Modular Control Plane (MCP) Server** built with **FastAPI** and **Microsoft Graph**.  
It helps automate real-world admin tasks in **Azure AD (Entra ID)** like checking user lockouts, verifying tenant health, and exploring sign-in trends.  
Everything runs safely inside your environment, no external data sharing involved.

---

## 🚀 What You Can Do

- **Ping Microsoft Graph**  
  Make sure your app registration and permissions are working.

- **Check user lockouts**  
  See if a user had repeated login failures, password issues, or conditional access blocks.

- **Add your own tools**  
  You can easily create new endpoints like Intune device checks or PIM activity audits.

---

## 🧩 How It Fits Together

```
        ┌──────────────────────────────┐
        │      User or AI Client       │
        └──────────────┬───────────────┘
                       │
                       ▼
             ┌─────────────────────┐
             │   MCP Server (API)  │
             │  FastAPI + Python   │
             └─────────┬───────────┘
                       │
                       ▼
       ┌────────────────────────────┐
       │  Microsoft Graph API Layer │
       └──────────────┬─────────────┘
                       │
                       ▼
            ┌──────────────────────┐
            │ Entra ID / Azure AD  │
            └──────────────────────┘
```

Everything runs locally or in your tenant. No outside calls, no surprises.

---

## 🛠 Prerequisites

| Requirement | Description |
|--------------|-------------|
| Python 3.10+ | Tested on 3.10.6 |
| Azure AD App | Needs `AuditLog.Read.All` and `Directory.Read.All` (Application permission) |
| OS | Windows, Linux, or macOS |

---

## ⚙️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/<your-username>/mcp-server.git
cd mcp-server
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

### 4. Create a `.env` file
```bash
TENANT_ID=<your-tenant-id>
CLIENT_ID=<your-app-client-id>
CLIENT_SECRET=<your-client-secret>
```

### 5. Run the server
```bash
uvicorn server:app --reload
```

### 6. Test endpoints
```bash
# Check health
Invoke-RestMethod http://127.0.0.1:8000/health

# List tools
Invoke-RestMethod http://127.0.0.1:8000/tools

# Ping Graph
$body = @{ tool="graph_ping"; input=@{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/run -Body $body -ContentType "application/json"
```

---

## 🧪 Sample Output

```json
{
  "tool": "entra_user_lockout",
  "result": {
    "upn": "alice@contoso.com",
    "status": "ok_after_failures",
    "failure_count": 2,
    "success_count": 8,
    "last_failure_time": "2025-10-16T14:14:32Z",
    "last_success_time": "2025-10-16T16:37:02Z",
    "recommendation": "No action needed. User signed in successfully later."
  }
}
```

---

## 🧰 Add Your Own Tools

Example:
```python
@log_tool
def my_custom_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Your logic here
    return {"message": "Hello from your custom tool!"}
```

Add it to `mcp_tools.py`, and it automatically shows up in `/tools` and `/run`.

---

## 🧑‍💻 Author

**Sai Vibhav Gudala**  
Cloud & Identity Engineer • Azure & Entra ID • Infra Automation Enthusiast  
📍 Exploring how AI agents can automate real-world IT operations

---

## 🪪 License

MIT License. Use it, modify it, break it, fix it. Just don’t expose your secrets.

---

## 🎯 Tip

Want to go further? Try adding tools for:
- Intune device compliance
- Conditional Access drift detection
- PIM assignment review

You’ll learn a ton about Graph and Entra automation.
