# server.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any
from mcp_tools import list_tools, run_tool

app = FastAPI(title="MCP Mini Server")

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "FastAPI is running"}

@app.get("/tools")
def get_tools():
    return list_tools()

class RunRequest(BaseModel):
    tool: str
    input: Dict[str, Any] = {}

@app.post("/run")
def run(req: RunRequest):
    try:
        result = run_tool(req.tool, req.input)
        return JSONResponse(content={"tool": req.tool, "result": result})
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Surface safe error text; logs can hold more later
        raise HTTPException(status_code=500, detail=str(e))
