"""
FastAPI backend. In-memory store is intentional for the MVP scope (Section 5:
"complete vertical slice rather than a collection of unfinished features") —
swap for a real DB in Phase 4 (Backend & Data) without touching the graph.
"""
import uuid
import threading
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .api_adapter import build_router, mount_frontend
from .auth import default_auth_service
from .auth_api import build_auth_router
from .graph import workflow_graph

app = FastAPI(
    title="intelligenzit AI Workforce Platform — MVP",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before pilot (Section 14)
    allow_methods=["*"],
    allow_headers=["*"],
)

# request_id -> WorkflowState
_store: dict[str, dict] = {}
_lock = threading.Lock()


class SubmitRequest(BaseModel):
    text: str


def _run_workflow(request_id: str, text: str):
    initial_state = {
        "request_id": request_id,
        "raw_request": text,
        "status": "running",
        "log": [],
    }
    with _lock:
        _store[request_id] = initial_state
    try:
        final_state = workflow_graph.invoke(initial_state)
        final_state["status"] = "complete"
    except Exception as e:
        final_state = _store[request_id]
        final_state["status"] = "failed"
        final_state.setdefault("log", []).append({"stage": "error", "summary": str(e)})
    with _lock:
        _store[request_id] = final_state


@app.post("/submit", dependencies=[Depends(default_auth_service.require("operator"))])
def submit(req: SubmitRequest):
    request_id = str(uuid.uuid4())[:8]
    thread = threading.Thread(target=_run_workflow, args=(request_id, req.text), daemon=True)
    thread.start()
    return {"request_id": request_id, "status": "running"}


@app.get("/status/{request_id}", dependencies=[Depends(default_auth_service.require("viewer"))])
def status(request_id: str):
    with _lock:
        state = _store.get(request_id)
    if not state:
        raise HTTPException(404, "Unknown request_id")
    return {
        "request_id": request_id,
        "status": state.get("status", "running"),
        "log": state.get("log", []),
    }


@app.get("/result/{request_id}", dependencies=[Depends(default_auth_service.require("viewer"))])
def result(request_id: str):
    with _lock:
        state = _store.get(request_id)
    if not state:
        raise HTTPException(404, "Unknown request_id")
    if state.get("status") != "complete":
        raise HTTPException(409, f"Not complete yet (status={state.get('status')})")
    return {
        "request_id": request_id,
        "request_type": state.get("request_type"),
        "final_report": state.get("final_report"),
        "review_decision": state.get("review_decision"),
        "rework_count": state.get("rework_count"),
        "log": state.get("log", []),
    }


@app.get("/history", dependencies=[Depends(default_auth_service.require("viewer"))])
def history():
    with _lock:
        return [
            {"request_id": rid, "status": s.get("status"),
             "raw_request": s.get("raw_request", "")[:120]}
            for rid, s in _store.items()
        ]


# ---------------------------------------------------------------------------
# Operator interface: the /api/* adapter (executes via AIExecutive) and the
# static frontend served same-origin. Every data route above and below is
# role-protected (viewer/operator/admin); only /health, /api/auth/login,
# /api/auth/status, the API docs and the static UI files are public.
# ---------------------------------------------------------------------------
default_auth_service.bootstrap_from_env()
app.include_router(build_auth_router(default_auth_service))
app.include_router(build_router(auth=default_auth_service))
mount_frontend(app)
