# 🧠 MCP Server — Azure & Entra ID Automation Framework

This project is a **Modular Control Plane (MCP) Server** built with **FastAPI** and **Microsoft Graph**.  
It automates real-world administrative tasks across **Azure AD (Entra ID)** — like checking user lockouts, tenant health, and authentication issues — while keeping data fully inside your environment.

---

## 🚀 Features

- **Graph Connectivity Test (`/graph_ping`)**  
  Validates your Microsoft Graph access and returns your tenant name & ID.

- **User Lockout Analyzer (`/entra_user_lockout`)**  
  Summarizes recent user sign-ins, failures, error codes, and Conditional Access status.

- **Secure Local Deployment**  
  Uses `.env` for secrets and runs in a sandboxed Python virtual environment.

- **Extensible MCP Tooling**  
  Add your own tools easily — Intune health checks, CA policy evaluation, etc.

---

## 🧩 Architecture Overview

![Architecture Diagram](architecture.png)

```
User/AI → MCP Server (FastAPI)
                ↓
         Microsoft Graph API
                ↓
          Entra ID / Azure AD
```

All traffic stays within your tenant — no external logging or cloud relay.

---

## 🛠️ Prerequisites

| Requirement | Description |
|--------------|-------------|
| Python 3.10+ | Tested on 3.10.6 |
| Azure AD App Registration | Client credentials with `AuditLog.Read.All` + `Directory.Read.All` |
| Permissions Type | Application (not delegated) |
| OS | Windows or Linux (PowerShell / Bash) |

---

## ⚙️ Setup Instructions

### 1. Clone the repo
```bash
git clone https://github.com/<your-username>/mcp-server.git
cd mcp-server
```

### 2. Create a virtual environment
```bash
python -m venv .venv
.\.venv\Scripts\activate        # Windows
# source .venv/bin/activate      # Linux/macOS
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
Create a file named `.env` in the project root:
```bash
TENANT_ID=<your-tenant-id>
CLIENT_ID=<your-app-client-id>
CLIENT_SECRET=<your-client-secret>
```

### 5. Run the server
```bash
uvicorn server:app --reload
```

### 6. Test it
```bash
# Health check
Invoke-RestMethod http://127.0.0.1:8000/health

# List available tools
Invoke-RestMethod http://127.0.0.1:8000/tools

# Run Graph Ping
$body = @{ tool="graph_ping"; input=@{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/run -Body $body -ContentType "application/json"
```

---

## 🧪 Example Output

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
    "recommendation": "No action. Recent successes observed."
  }
}
```

---

## 🧰 Add Your Own Tools

Each tool is a simple Python function with:
```python
@log_tool
def my_custom_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    # logic here
    return {"result": "ok"}
```

Register it in `mcp_tools.py` and it automatically appears in `/tools` and `/run`.

---

## 🧑‍💻 Author

**Sai Vibhav Gudala**  
Cloud & Identity Engineer | Hybrid Infrastructure | Microsoft Entra ID & Azure Automation  
📍 Currently exploring AI + Infra Automation through MCP architectures

---

## 🛡️ License

MIT License — free to use, modify, and share.

---

## 💬 Want to Try It?

If you have Azure access, you can use your own Entra app credentials.  
Or fork the repo and build your own tools — e.g.  
- `entra_device_health`  
- `intune_compliance_summary`  
- `pim_role_audit`

Pull requests and suggestions are welcome!
