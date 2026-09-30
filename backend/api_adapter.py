"""
HTTP adapter for the operator frontend (frontend/).

The frontend speaks a small `/api/*` contract; this module translates it onto
the current backend without duplicating any of its logic:

    frontend  ->  /api/*  ->  AdapterRuntime  ->  AIExecutive.dispatch()
                                                   (AgentRegistry, Memory,
                                                    KnowledgeBase, ToolRegistry,
                                                    n8n, Audit, LangGraph)

Design rules:
- Workflow execution always goes through AIExecutive, never workflow_graph
  directly. Live progress comes from AIExecutive's optional on_progress hook.
- Nothing here changes LangGraph, WorkflowState or any agent. The adapter only
  reads what the existing modules already expose.
- Read-only views (agents, memory, governance, audit, health) read the real
  registries/stores. Where the current backend simply has no equivalent data
  (e.g. quality scores, structured research dossiers, token counts) the
  adapter returns an honest empty/zero value instead of inventing one.
- Engagement runs are written through to a RunStore (data/runs.sqlite3, its
  own database) after every change, and reloaded when the runtime starts, so
  workflow history, the dashboard and metrics survive a restart. A run that
  was still queued/running when the process stopped is marked failed
  ("interrupted"), never silently resumed or shown as running forever.
  Without a RunStore the runtime is memory-only.
- No authentication, no secrets: nothing returned here contains an API key,
  a webhook URL, a prompt or a raw model response beyond the stage outputs the
  workflow itself produced for the operator.
"""
import re
import threading
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm as llm_module
from .agents import MAX_REWORKS
from .ai_executive import AIExecutive
from .auth import AuthService
from .run_store import RunStore, default_run_store

PRODUCT_NAME = "Intelligenzit AI Workforce Platform"
VERSION = "1.0"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The LangGraph node names (backend/graph.py), in execution order, with the
# registry agent that runs each one.
PIPELINE = [
    {"id": "orchestrate", "label": "Orchestration", "agent": "orchestrator"},
    {"id": "research", "label": "Research", "agent": "research"},
    {"id": "requirements", "label": "Requirements", "agent": "requirements"},
    {"id": "analysis", "label": "Analysis", "agent": "analysis"},
    {"id": "solution", "label": "Solution", "agent": "solution"},
    {"id": "review", "label": "Quality Review", "agent": "review"},
    {"id": "report", "label": "Final Report", "agent": "report"},
]
_STAGE_BY_ID = {s["id"]: s for s in PIPELINE}
_STAGE_BY_AGENT = {s["agent"]: s for s in PIPELINE}

_STAGE_OUTPUT_KEY = {
    "research": "research_findings",
    "requirements": "requirements",
    "analysis": "analysis",
    "solution": "solution",
    "review": "review_notes",
    "report": "final_report",
}

_DENIAL_STATUSES = {"unknown_tool", "disabled", "invalid_input", "invalid_method", "invalid_payload"}
_OUTPUT_LIMIT = 4000


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "crossfunctional"


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (ConnectionError, OSError)):
        return "The language model provider could not be reached."
    return f"The workflow stopped unexpectedly ({type(exc).__name__})."


def _last_log_summary(state: dict, node: str) -> str:
    for entry in reversed(state.get("log") or []):
        if entry.get("stage") == node:
            return str(entry.get("summary") or "")
    return ""


# ---------------------------------------------------------------------------
# provider status (never exposes credentials)
# ---------------------------------------------------------------------------
_probe_cache: dict = {}


def _probe_ollama(host: str) -> bool:
    try:
        import ollama
        ollama.Client(host=host, timeout=1.5).list()
        return True
    except Exception:
        return False


def _provider_reachable(provider: str) -> Optional[bool]:
    """True/False for a local Ollama probe (cached briefly); None when the
    provider is remote and is deliberately not probed from a status check."""
    if provider != "ollama":
        return None
    import os
    host = os.environ.get("OLLAMA_HOST", llm_module.DEFAULT_OLLAMA_HOST)
    cached = _probe_cache.get(host)
    if cached and time.monotonic() - cached[0] < 10:
        return cached[1]
    reachable = _probe_ollama(host)
    _probe_cache[host] = (time.monotonic(), reachable)
    return reachable


def provider_status() -> dict:
    import os
    if llm_module._mock_mode():
        return {
            "provider": "mock", "supported_providers": ["ollama", "anthropic"],
            "model": "mock", "credentials_configured": True, "active_engine": "mock",
            "reachable": True, "model_available": None,
            "detail": "MOCK_MODE is enabled: canned responses, no model calls are made.",
        }
    provider = llm_module._provider()
    reachable = _provider_reachable(provider)
    credentials = bool(os.environ.get("ANTHROPIC_API_KEY")) if provider == "anthropic" else True
    if reachable is False:
        detail = f"The {provider} provider is not reachable."
    elif provider == "anthropic" and not credentials:
        detail = "No API key is configured for this provider."
    else:
        detail = ""
    return {
        "provider": provider, "supported_providers": ["ollama", "anthropic"],
        "model": llm_module._default_model(provider), "credentials_configured": credentials,
        "active_engine": provider, "reachable": reachable, "model_available": None,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------
class SubmitBody(BaseModel):
    objective: str = Field(..., min_length=1, max_length=8000)
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    department: str = ""
    expected_outcome: str = ""
    constraints: str = ""
    submitted_by: str = "platform"


class DecisionBody(BaseModel):
    decision: str
    reviewer: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# runtime: engagement runs, driven through AIExecutive
# ---------------------------------------------------------------------------
class AdapterRuntime:
    def __init__(self, executive: AIExecutive, *, spawn: Optional[Callable] = None,
                 run_store: Optional[RunStore] = None):
        self.executive = executive
        self.run_store = run_store
        self._spawn = spawn or _spawn_thread
        self._runs: dict = {}
        self._order: list = []
        self._lock = threading.RLock()
        self._reload()

    # -- persistence ----------------------------------------------------------
    def _persist(self, run: dict) -> None:
        if self.run_store is None:
            return
        done = sum(1 for s in run["stages"] if s["status"] in ("completed", "skipped"))
        run["progress"] = _pct(done, len(run["stages"]))
        try:
            self.run_store.save(run)
        except Exception as exc:  # RunStore fails safe itself; this is a second net.
            warnings.warn(f"api_adapter: persisting run failed ({exc})", RuntimeWarning)

    def _reload(self) -> None:
        """Load previously stored runs. Anything still queued/running was cut
        off by the restart: mark it failed rather than leave it "running"."""
        if self.run_store is None:
            return
        for run in self.run_store.load_all():
            self._runs[run["request_id"]] = run
            self._order.append(run["request_id"])
            if run["status"] in ("queued", "running"):
                self._interrupt(run)

    def _interrupt(self, run: dict) -> None:
        for stage in run["stages"]:
            if stage["status"] == "running":
                stage["status"] = "failed"
        run["error"] = "This engagement was interrupted by a service restart before it finished."
        self._finish_common(run, "failed")
        self._event(run, run["error"], level="error", agent_id="ai_executive")
        self._persist(run)
        try:
            self.executive.audit_store.append(
                actor="ai_executive", action="dispatch", status="failed",
                request_id=run["request_id"], metadata={"error_type": "Interrupted"},
            )
        except Exception as audit_exc:  # audit must never break startup
            warnings.warn(f"api_adapter: audit logging failed ({audit_exc})", RuntimeWarning)

    # -- registry views ----------------------------------------------------
    def _agent_department(self, agent_id: str) -> str:
        agent = self.executive.registry.get_or_none(agent_id)
        return _slug(agent.department) if agent else ""

    def _agent_definitions(self) -> list:
        return self.executive.registry.list_agents()

    # -- submission ---------------------------------------------------------
    def submit(self, body: SubmitBody) -> dict:
        request_id = str(uuid.uuid4())[:8]
        text = body.objective.strip()
        extras = []
        if body.expected_outcome.strip():
            extras.append(f"Expected outcome: {body.expected_outcome.strip()}")
        if body.constraints.strip():
            extras.append(f"Constraints: {body.constraints.strip()}")
        submitted_at = _now()

        stages = [
            {
                "stage_id": s["id"], "label": s["label"], "agent_id": s["agent"],
                "department": self._agent_department(s["agent"]), "status": "pending",
                "attempt": 0, "started_at": None, "completed_at": None,
                "duration_ms": None, "detail": "",
            }
            for s in PIPELINE
        ]
        run = {
            "request_id": request_id, "status": "queued",
            "user_request": body.objective.strip(),
            "_task": "\n\n".join([text, *extras]),
            "priority": body.priority, "department": body.department,
            "submitted_by": body.submitted_by,
            "created_at": submitted_at, "started_at": None, "completed_at": None,
            "duration_ms": None, "_t0": None, "_stage_t0": {},
            "current_stage": "", "stages": stages, "events": [], "tasks": [],
            "assignments": [], "memory_context": [], "state": {}, "request_type": "",
            "solution_drafts": [], "review_history": [], "agent_runs": [],
            "error": "", "engine": provider_status()["active_engine"],
        }
        with self._lock:
            self._runs[request_id] = run
            self._order.append(request_id)
            self._persist(run)
        self._spawn(lambda: self._execute(request_id))
        return {"request_id": request_id, "status": "queued", "submitted_at": submitted_at}

    # -- execution ----------------------------------------------------------
    def _event(self, run: dict, message: str, *, level: str = "info", stage: str = "",
               agent_id: str = "") -> None:
        run["events"].append({
            "id": len(run["events"]) + 1, "ts": _now(), "stage": stage, "agent_id": agent_id,
            "department": self._agent_department(agent_id) if agent_id else "",
            "level": level, "message": message, "payload": None,
        })

    def _execute(self, request_id: str) -> None:
        with self._lock:
            run = self._runs[request_id]
            run["status"] = "running"
            run["started_at"] = _now()
            run["_t0"] = time.monotonic()
            self._event(run, "Engagement started", agent_id="ai_executive")
            self._persist(run)

        def on_progress(kind: str, payload: dict) -> None:
            with self._lock:
                target = self._runs[request_id]
                getattr(self, f"_on_{kind}")(target, **payload)
                self._persist(target)

        try:
            result = self.executive.dispatch(run["_task"], request_id=request_id, on_progress=on_progress)
        except Exception as exc:
            self._fail(request_id, exc)
            return
        self._complete(request_id, result.workflow_result)

    def _on_plan(self, run: dict, plan) -> None:
        registry = self.executive.registry
        for step in plan.steps:
            agent = registry.get_or_none(step.agent_id)
            run["tasks"].append({
                "title": agent.name if agent else step.agent_id, "acceptance": step.reason,
                "department": _slug(agent.department) if agent else "",
                "agent_id": step.agent_id, "status": "pending",
            })
            run["assignments"].append({
                "task_title": agent.name if agent else step.agent_id,
                "capability": agent.capabilities[0] if agent and agent.capabilities else "",
                "agent_id": step.agent_id, "agent_name": agent.name if agent else step.agent_id,
                "reason": step.reason,
            })
        for record in plan.retrieved_memory:
            run["memory_context"].append({
                "title": (record.task or "")[:80], "kind": "deliverable",
                "content": record.content, "request_id": (record.metadata or {}).get("request_id") or "",
            })
        for chunk in plan.retrieved_knowledge:
            run["memory_context"].append({
                "title": f"{chunk.filename} (chunk {chunk.chunk_index})", "kind": "knowledge",
                "content": chunk.text, "request_id": "",
            })
        note = " Default pipeline used." if plan.fallback_used else ""
        self._event(run, f"Execution plan ready: {len(plan.steps)} agents ({plan.task_type}).{note}",
                    agent_id="ai_executive")
        if plan.retrieved_memory or plan.retrieved_knowledge:
            self._event(
                run,
                f"Retrieved {len(plan.retrieved_memory)} memory record(s) and "
                f"{len(plan.retrieved_knowledge)} knowledge chunk(s) as planning context.",
                agent_id="ai_executive",
            )

    def _on_stage_started(self, run: dict, node: str) -> None:
        stage = next((s for s in run["stages"] if s["stage_id"] == node), None)
        if stage is None:
            return
        stage["status"] = "running"
        stage["attempt"] += 1
        stage["started_at"] = _now()
        stage["completed_at"] = None
        run["_stage_t0"][node] = time.monotonic()
        run["current_stage"] = node
        self._event(run, f"{stage['label']} started", level="debug", stage=node, agent_id=stage["agent_id"])

    def _on_stage_completed(self, run: dict, node: str, state: dict) -> None:
        stage = next((s for s in run["stages"] if s["stage_id"] == node), None)
        if stage is None:
            return
        started = run["_stage_t0"].get(node)
        stage["duration_ms"] = int((time.monotonic() - started) * 1000) if started else None
        stage["status"] = "completed"
        stage["completed_at"] = _now()
        stage["detail"] = _last_log_summary(state, node)
        if node == "orchestrate":
            run["request_type"] = str(state.get("request_type") or "")

        level, message = "info", f"{stage['label']} completed"
        if node == "solution":
            run["solution_drafts"].append({"attempt": len(run["solution_drafts"]) + 1,
                                           "text": state.get("solution", "")})
        elif node == "review":
            decision = "rework" if state.get("review_decision") == "rework" else "approve"
            run["review_history"].append({
                "attempt": len(run["review_history"]) + 1,
                "decision": decision, "summary": state.get("review_notes", ""),
                "findings": [], "required_changes": [], "scores": {},
                "rework_target": "solution" if decision == "rework" else "",
            })
            if decision == "rework":
                level, message = "warn", "Quality Review returned the solution for rework"
            else:
                message = "Quality Review approved the solution"

        output = state.get(_STAGE_OUTPUT_KEY.get(node, ""), "") if node in _STAGE_OUTPUT_KEY \
            else f"Request type: {state.get('request_type', '')}"
        run["agent_runs"].append({
            "id": len(run["agent_runs"]) + 1, "request_id": run["request_id"], "node": node,
            "agent_id": stage["agent_id"], "status": "completed",
            "duration_ms": stage["duration_ms"], "started_at": stage["started_at"],
            "completed_at": stage["completed_at"], "engine": run["engine"],
            "output": str(output or "")[:_OUTPUT_LIMIT], "error": "",
        })
        self._event(run, message, level=level, stage=node, agent_id=stage["agent_id"])

    def _finish_common(self, run: dict, status: str) -> None:
        run["status"] = status
        run["completed_at"] = _now()
        run["current_stage"] = ""
        if run["_t0"] is not None:
            run["duration_ms"] = int((time.monotonic() - run["_t0"]) * 1000)
        for stage in run["stages"]:
            if stage["status"] == "pending":
                stage["status"] = "skipped"

    def _complete(self, request_id: str, state: dict) -> None:
        with self._lock:
            run = self._runs[request_id]
            run["state"] = state
            run["request_type"] = str(state.get("request_type") or run["request_type"])
            if state.get("status") == "complete":
                self._finish_common(run, "completed")
                self._event(run, "Engagement completed", agent_id="ai_executive")
            else:
                run["error"] = "The workflow ended before producing a deliverable."
                self._finish_common(run, "failed")
                self._event(run, run["error"], level="error", agent_id="ai_executive")
            self._persist(run)

    def _fail(self, request_id: str, exc: Exception) -> None:
        with self._lock:
            run = self._runs[request_id]
            for stage in run["stages"]:
                if stage["status"] == "running":
                    stage["status"] = "failed"
            run["error"] = _safe_error(exc)
            self._finish_common(run, "failed")
            self._event(run, run["error"], level="error", agent_id="ai_executive")
            self._persist(run)
        try:
            self.executive.audit_store.append(
                actor="ai_executive", action="dispatch", status="failed",
                request_id=request_id, metadata={"error_type": type(exc).__name__},
            )
        except Exception as audit_exc:  # audit must never break the API
            warnings.warn(f"api_adapter: audit logging failed ({audit_exc})", RuntimeWarning)

    # -- reads ----------------------------------------------------------------
    def _get(self, request_id: str) -> dict:
        with self._lock:
            run = self._runs.get(request_id)
        if run is None:
            raise HTTPException(404, f"No engagement exists with id '{request_id}'.")
        return run

    @staticmethod
    def _rework_count(run: dict) -> int:
        # Counted from observed review passes, independent of state["rework_count"].
        return sum(1 for r in run["review_history"] if r["decision"] == "rework")

    @staticmethod
    def _final_review_decision(run: dict) -> str:
        return run["review_history"][-1]["decision"] if run["review_history"] else ""

    def _summary_row(self, run: dict) -> dict:
        return {
            "request_id": run["request_id"], "user_request": run["user_request"],
            "status": run["status"], "priority": run["priority"],
            "request_type": run["request_type"], "created_at": run["created_at"],
            "started_at": run["started_at"], "completed_at": run["completed_at"],
            "duration_ms": run["duration_ms"],
            "rework_count": self._rework_count(run), "current_stage": run["current_stage"],
            "engine": run["engine"],
        }

    def _task_status(self, run: dict, agent_id: str) -> str:
        stage = _STAGE_BY_AGENT.get(agent_id)
        if stage is None:
            return "pending"
        current = next((s for s in run["stages"] if s["stage_id"] == stage["id"]), None)
        if current is None:
            return "pending"
        return current["status"] if current["status"] in ("running", "completed") else "pending"

    def status_payload(self, request_id: str, after_event: int = 0) -> dict:
        run = self._get(request_id)
        with self._lock:
            stages = [dict(s) for s in run["stages"]]
            done = sum(1 for s in stages if s["status"] in ("completed", "skipped"))
            return {
                "request_id": request_id, "status": run["status"],
                "current_stage": run["current_stage"], "objective": run["user_request"],
                "priority": run["priority"], "created_at": run["created_at"],
                "started_at": run["started_at"], "completed_at": run["completed_at"],
                "duration_ms": run["duration_ms"],
                "rework_count": self._rework_count(run),
                "review_decision": self._final_review_decision(run),
                "engine": run["engine"], "request_type": run["request_type"],
                "progress": _pct(done, len(stages)), "stages": stages,
                "events": [e for e in run["events"] if e["id"] > after_event],
                "tasks": [{**t, "status": self._task_status(run, t["agent_id"])} for t in run["tasks"]],
                "error": run["error"],
            }

    def result_payload(self, request_id: str) -> dict:
        run = self._get(request_id)
        if run["status"] != "completed":
            raise HTTPException(
                409, f"This engagement is {run['status']}. Poll the status endpoint until it "
                     "reaches 'completed'.")
        state = run["state"]
        final = self._final_review_decision(run)
        open_items = [state.get("review_notes")] if final == "rework" and state.get("review_notes") else []
        return {
            "request_id": run["request_id"], "status": run["status"],
            "objective": run["user_request"], "request_type": run["request_type"],
            "duration_ms": run["duration_ms"],
            "rework_count": self._rework_count(run), "engine": run["engine"],
            "tasks": run["tasks"], "assignments": run["assignments"],
            "research": state.get("research_findings", ""),
            "data_requirements": state.get("requirements", ""),
            "analysis": state.get("analysis", ""),
            "strategy": state.get("solution", ""),
            "solution_drafts": run["solution_drafts"],
            "review_history": run["review_history"],
            "executive_decision": {
                "decision": "approved_with_conditions" if final == "rework" else "approved",
                "rationale": f"Released after {len(run['review_history'])} review pass(es).",
                "open_items": open_items, "requires_human_signoff": False,
            },
            "final_proposal": state.get("final_report", ""),
            "execution_log": state.get("log", []),
            "memory_context": run["memory_context"],
        }

    def list_payload(self, limit: int, offset: int, status: Optional[str]) -> dict:
        with self._lock:
            rows = [self._summary_row(self._runs[r]) for r in reversed(self._order)]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return {"total": len(rows), "items": rows[offset: offset + limit]}

    def detail_payload(self, request_id: str) -> dict:
        run = self._get(request_id)
        payload = {"summary": self.status_payload(request_id),
                   "agent_runs": list(run["agent_runs"])}
        if run["status"] == "completed":
            payload["result"] = self.result_payload(request_id)
        return payload

    def research_payload(self, request_id: str) -> dict:
        run = self._get(request_id)
        stage = next((s for s in run["stages"] if s["stage_id"] == "research"), {"status": "pending"})
        timeline = [
            {"label": e["message"], "status": "completed" if e["level"] != "debug" else "",
             "at": e["ts"], "detail": ""}
            for e in run["events"] if e["stage"] == "research" and e["level"] != "debug"
        ]
        return {
            "request_id": request_id, "objective": run["user_request"],
            "workflow_status": run["status"], "stage_status": stage["status"],
            "available": False,
            "reason": "The current research agent produces a narrative summary rather than a "
                      "structured dossier. That narrative is shown on the workflow page "
                      "(Research tab) and in the deliverable.",
            "timeline": timeline,
        }

    # -- aggregate views --------------------------------------------------------
    def _all_runs(self) -> list:
        with self._lock:
            return [self._runs[r] for r in self._order]

    def summary(self) -> dict:
        runs = self._all_runs()
        completed = [r for r in runs if r["status"] == "completed"]
        failed = [r for r in runs if r["status"] == "failed"]
        finished = len(completed) + len(failed)
        reviewed = [r for r in runs if r["review_history"]]
        with_rework = [r for r in reviewed if self._rework_count(r) > 0]
        agent_runs = [a for r in runs for a in r["agent_runs"]]
        durations = [r["duration_ms"] for r in completed if r["duration_ms"] is not None]
        return {
            "has_data": bool(runs), "total_workflows": len(runs),
            "active_workflows": sum(1 for r in runs if r["status"] in ("queued", "running")),
            "completed_workflows": len(completed), "failed_workflows": len(failed),
            "total_workflows_finished": finished,
            "success_rate": _pct(len(completed), finished), "failure_rate": _pct(len(failed), finished),
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            "reviewed_workflows": len(reviewed),
            "workflows_with_rework": len(with_rework),
            "total_reworks": sum(self._rework_count(r) for r in runs),
            "rework_rate": _pct(len(with_rework), len(reviewed)),
            "review_pass_first_time_rate": _pct(len(reviewed) - len(with_rework), len(reviewed)),
            "agent_runs": len(agent_runs),
            "failed_agent_runs": sum(1 for a in agent_runs if a["status"] == "failed"),
            "agent_run_failure_rate": _pct(sum(1 for a in agent_runs if a["status"] == "failed"),
                                           len(agent_runs)),
            "input_tokens": 0, "output_tokens": 0,
        }

    def _working_agents(self) -> set:
        working = set()
        for run in self._all_runs():
            if run["status"] == "running":
                working.update(s["agent_id"] for s in run["stages"] if s["status"] == "running")
        return working

    def agent_leaderboard(self) -> list:
        agent_runs = [a for r in self._all_runs() for a in r["agent_runs"]]
        working = self._working_agents()
        board = []
        for agent in self._agent_definitions():
            mine = [a for a in agent_runs if a["agent_id"] == agent.agent_id]
            done = [a for a in mine if a["status"] == "completed"]
            durations = [a["duration_ms"] for a in mine if a["duration_ms"] is not None]
            executes = agent.agent_id in _STAGE_BY_AGENT
            board.append({
                "agent_id": agent.agent_id, "name": agent.name, "title": agent.role,
                "department": _slug(agent.department), "executes": executes,
                "status": "working" if agent.agent_id in working else ("idle" if executes else "standby"),
                "runs": len(mine), "completed": len(done), "failed": len(mine) - len(done),
                "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
                "reliability": round(100 * len(done) / len(mine)) if mine else None,
                "last_active": max((a["completed_at"] for a in mine if a["completed_at"]), default=None),
            })
        return board

    def stage_performance(self) -> list:
        agent_runs = [a for r in self._all_runs() for a in r["agent_runs"]]
        rows = []
        for stage in PIPELINE:
            mine = [a for a in agent_runs if a["node"] == stage["id"] and a["duration_ms"] is not None]
            rows.append({
                "node": stage["id"], "label": stage["label"], "agent": stage["agent"],
                "runs": len(mine),
                "avg_duration_ms": int(sum(a["duration_ms"] for a in mine) / len(mine)) if mine else 0,
            })
        return [r for r in rows if r["runs"]]

    def throughput(self, days: int = 14) -> list:
        buckets: dict = {}
        for run in self._all_runs():
            if run["status"] not in ("completed", "failed"):
                continue
            day = run["created_at"][:10]
            bucket = buckets.setdefault(day, {"day": day, "total": 0, "completed": 0, "failed": 0})
            bucket["total"] += 1
            bucket[run["status"]] += 1
        return [buckets[d] for d in sorted(buckets)][-days:]

    def department_load(self) -> list:
        load: dict = {}
        for row in self.agent_leaderboard():
            if row["runs"]:
                load[row["department"]] = load.get(row["department"], 0) + row["runs"]
        return [{"department": d, "runs": n} for d, n in sorted(load.items())]

    @staticmethod
    def research_stats() -> dict:
        return {
            "has_data": False, "engagements_researched": 0, "dossiers": 0, "avg_confidence": 0,
            "questions": 0, "questions_answered": 0, "question_completion_rate": 0,
            "findings": 0, "open_gaps": 0, "gaps_per_engagement": 0, "followup_rate": 0,
            "followup_rounds": 0, "confidence_series": [],
        }

    def recent_events(self, limit: int = 14) -> list:
        events = [
            {**e, "request_id": r["request_id"]}
            for r in self._all_runs() for e in r["events"] if e["level"] != "debug"
        ]
        events.sort(key=lambda e: e["ts"])
        return events[-limit:]


# ---------------------------------------------------------------------------
# static views over the existing stores
# ---------------------------------------------------------------------------
def _audit_entry(event) -> dict:
    status = event.status
    if status in ("success", "complete", "completed"):
        outcome = "ok"
    elif status in _DENIAL_STATUSES:
        outcome = "denied"
    elif status in ("failed", "execution_error", "network_error", "http_error", "timeout"):
        outcome = "error"
    else:
        outcome = status
    detail = ", ".join(f"{k}={v}" for k, v in (event.metadata or {}).items())
    return {
        "id": event.id, "ts": event.timestamp,
        "target": event.request_id or event.tool_id or "", "actor": event.actor,
        "action": event.action, "outcome": outcome, "detail": detail[:300],
    }


def _stores_available(executive: AIExecutive) -> bool:
    return all(s.is_available for s in (executive.memory, executive.knowledge_base, executive.audit_store))


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _memory_entry(record, relevance: float = 0.0) -> dict:
    return {
        "id": record.id, "kind": "deliverable", "title": (record.task or "")[:100],
        "content": record.content, "tags": list((record.metadata or {}).get("selected_agents") or []),
        "confidence": round(relevance, 2), "created_at": record.created_at,
        "request_id": (record.metadata or {}).get("request_id") or "",
    }


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------
def build_router(runtime: Optional[AdapterRuntime] = None, auth: Optional[AuthService] = None) -> APIRouter:
    if runtime is None:
        from .ai_executive import default_executive
        runtime = AdapterRuntime(default_executive, run_store=default_run_store)
    rt = runtime
    ex = rt.executive
    router = APIRouter()
    enforced = auth is not None and auth.enabled

    def guard(role: str) -> list:
        """Route dependencies enforcing a minimum role. Without an AuthService the
        router is unprotected (used when embedding/testing the adapter alone);
        the application always builds it with one. /health is deliberately never guarded."""
        return [Depends(auth.require(role))] if auth is not None else []

    def graph_topology() -> dict:
        edges = [{"from": a["id"], "to": b["id"]} for a, b in zip(PIPELINE, PIPELINE[1:])
                 if a["id"] != "review"]
        edges += [
            {"from": "review", "to": "solution", "condition": "rework"},
            {"from": "review", "to": "report", "condition": "approve"},
            {"from": "report", "to": "end"},
        ]
        return {
            "entry": "orchestrate",
            "nodes": [{"id": s["id"], "agent": s["agent"],
                       "kind": "gate" if s["id"] == "review" else "agent"} for s in PIPELINE],
            "edges": edges,
        }

    @router.get("/health", tags=["platform"])
    def health() -> dict:
        provider = provider_status()
        database_ok = _stores_available(ex) and (rt.run_store is None or rt.run_store.is_available)
        healthy = database_ok and provider["reachable"] is not False
        agents = rt._agent_definitions()
        return {
            "status": "ok" if healthy else "degraded", "product": PRODUCT_NAME, "version": VERSION,
            "auth": "enabled" if enforced else "disabled",
            "database": "ok" if database_ok else "unavailable", "llm": provider,
            "active_workflows": rt.summary()["active_workflows"],
            "agents_registered": len(agents),
            "departments": len({a.department for a in agents}),
        }

    @router.get("/api/config", tags=["platform"], dependencies=guard("viewer"))
    def config() -> dict:
        provider = provider_status()
        return {
            "product_name": PRODUCT_NAME, "version": VERSION,
            "stages": [dict(s) for s in PIPELINE], "graph": graph_topology(),
            "max_rework_cycles": MAX_REWORKS,
            "require_human_approval": False, "api_key_required": enforced,
            "local_fallback_allowed": False,
            "provider": provider["provider"], "model": provider["model"],
            "credentials_configured": provider["credentials_configured"],
            "database": "data/*.sqlite3 (local SQLite)",
        }

    @router.get("/api/graph", tags=["organisation"], dependencies=guard("viewer"))
    def graph() -> dict:
        return graph_topology()

    # -- engagements -------------------------------------------------------
    @router.post("/api/workflows", tags=["workflows"], dependencies=guard("operator"))
    def create_workflow(body: SubmitBody, request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if user is not None:  # attribute the run to the signed-in user, not a client-supplied name
            body = body.model_copy(update={"submitted_by": user.username})
        return rt.submit(body)

    @router.get("/api/workflows", tags=["workflows"], dependencies=guard("viewer"))
    def list_workflows(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                       status: Optional[str] = None) -> dict:
        return rt.list_payload(limit, offset, status)

    @router.get("/api/workflows/{request_id}", tags=["workflows"], dependencies=guard("viewer"))
    def workflow_detail(request_id: str) -> dict:
        return rt.detail_payload(request_id)

    @router.get("/api/workflows/{request_id}/status", tags=["workflows"], dependencies=guard("viewer"))
    def workflow_status(request_id: str, after_event: int = Query(0, ge=0)) -> dict:
        return rt.status_payload(request_id, after_event)

    @router.get("/api/workflows/{request_id}/result", tags=["workflows"], dependencies=guard("viewer"))
    def workflow_result(request_id: str) -> dict:
        return rt.result_payload(request_id)

    @router.post("/api/workflows/{request_id}/decision", tags=["workflows"], dependencies=guard("operator"))
    def workflow_decision(request_id: str, body: DecisionBody) -> dict:
        rt._get(request_id)  # 404 for unknown ids
        raise HTTPException(
            409, "Human approval is not enabled on this platform: deliverables are released "
                 "automatically once quality review passes.")

    @router.get("/api/workflows/{request_id}/research", tags=["research"], dependencies=guard("viewer"))
    def workflow_research(request_id: str) -> dict:
        return rt.research_payload(request_id)

    @router.get("/api/research", tags=["research"], dependencies=guard("viewer"))
    def research_index(limit: int = Query(50, ge=1, le=200)) -> dict:
        return {
            "items": [], "stats": rt.research_stats(), "basis_types": {},
            "disclosure": "Research is produced by the configured language model from its own "
                          "knowledge, plus any organisational documents retrieved from the local "
                          "knowledge base. The platform performs no live web retrieval.",
        }

    # -- organisation ---------------------------------------------------------
    def agent_view(agent, stats: dict) -> dict:
        stage = _STAGE_BY_AGENT.get(agent.agent_id)
        view = {
            "id": agent.agent_id, "name": agent.name, "title": agent.role,
            "department": _slug(agent.department), "department_name": agent.department,
            "mission": agent.description, "capabilities": list(agent.capabilities),
            "permissions": [f"tool:{t}" for t in agent.tools], "executes": stage is not None,
            "seniority": "specialist", "node": stage["id"] if stage else None,
            "registry_status": agent.status.value,
        }
        view.update({k: stats.get(k) for k in
                     ("status", "runs", "completed", "failed", "avg_duration_ms", "reliability", "last_active")})
        return view

    @router.get("/api/agents", tags=["organisation"], dependencies=guard("viewer"))
    def list_agents() -> dict:
        board = {row["agent_id"]: row for row in rt.agent_leaderboard()}
        departments: dict = {}
        for agent in rt._agent_definitions():
            slug = _slug(agent.department)
            entry = departments.setdefault(slug, {
                "id": slug, "name": agent.department, "accent": slug, "order": len(departments),
                "agents": [],
            })
            entry["agents"].append(agent_view(agent, board[agent.agent_id]))
        for entry in departments.values():
            entry["mandate"] = "Roles: " + "; ".join(a["title"] for a in entry["agents"])
        chart = list(departments.values())
        return {
            "departments": chart,
            "totals": {
                "agents": sum(len(d["agents"]) for d in chart),
                "executing": sum(1 for d in chart for a in d["agents"] if a["executes"]),
                "departments": len(chart),
                "working_now": sum(1 for r in board.values() if r["status"] == "working"),
            },
        }

    @router.get("/api/agents/{agent_id}", tags=["organisation"], dependencies=guard("viewer"))
    def agent_detail(agent_id: str) -> dict:
        agent = ex.registry.get_or_none(agent_id)
        if agent is None:
            raise HTTPException(404, f"No agent registered with id '{agent_id}'.")
        board = {row["agent_id"]: row for row in rt.agent_leaderboard()}
        history = []
        for run in reversed(rt._all_runs()):
            for a in reversed(run["agent_runs"]):
                if a["agent_id"] == agent_id:
                    history.append({
                        "run_id": a["id"], "request_id": run["request_id"], "node": a["node"],
                        "status": a["status"], "duration_ms": a["duration_ms"],
                        "started_at": a["started_at"], "completed_at": a["completed_at"],
                        "engine": a["engine"], "objective": run["user_request"][:160],
                        "output": a["output"], "error": a["error"],
                    })
        peers = [agent_view(p, board[p.agent_id]) for p in rt._agent_definitions()
                 if p.department == agent.department and p.agent_id != agent_id]
        return {
            "agent": agent_view(agent, board[agent_id]), "stats": board[agent_id],
            "history": history[:15], "peers": peers,
        }

    # -- memory, metrics, dashboard, governance, audit ---------------------------
    @router.get("/api/memory", tags=["platform"], dependencies=guard("viewer"))
    def memory(limit: int = Query(100, ge=1, le=500), kind: Optional[str] = None,
               q: Optional[str] = None) -> dict:
        total = ex.memory.count()
        if q:
            query_tokens = _tokens(q)
            entries = [
                _memory_entry(r, len(query_tokens & (_tokens(r.task) | _tokens(r.content)))
                              / max(len(query_tokens), 1))
                for r in ex.memory.search(q, limit=limit)
            ]
        else:
            entries = [_memory_entry(r) for r in ex.memory.list_recent(limit)]
        if kind:
            entries = [e for e in entries if e["kind"] == kind]
        by_kind = {"deliverable": total} if total else {}
        payload = {"total": total, "by_kind": by_kind, "entries": entries}
        if q:
            payload["query"] = q
        return payload

    @router.get("/api/metrics", tags=["platform"], dependencies=guard("viewer"))
    def metrics() -> dict:
        return {
            "summary": rt.summary(), "agents": rt.agent_leaderboard(),
            "stages": rt.stage_performance(), "throughput": rt.throughput(),
            "department_load": rt.department_load(),
            "research": rt.research_stats(),
        }

    @router.get("/api/dashboard", tags=["platform"], dependencies=guard("viewer"))
    def dashboard() -> dict:
        board = rt.agent_leaderboard()
        provider = provider_status()
        return {
            "summary": rt.summary(),
            "agents_engaged": sum(1 for a in board if a["runs"] > 0), "agents_total": len(board),
            "engine": {"active_engine": provider["active_engine"], "provider": provider["provider"],
                       "model": provider["model"], "forced_local": False,
                       "last_engine_used": provider["active_engine"], "client_error": ""},
            "recent_workflows": rt.list_payload(8, 0, None)["items"],
            "recent_events": rt.recent_events(14), "agents": board[:8],
            "throughput": rt.throughput(), "departments": rt.department_load(),
            "research": rt.research_stats(),
            "active_workflows": rt.summary()["active_workflows"],
        }

    @router.get("/api/governance", tags=["platform"], dependencies=guard("admin"))
    def governance() -> dict:
        provider = provider_status()
        audit_entries = [_audit_entry(e) for e in ex.audit_store.recent(200)]
        summary = rt.summary()
        catalogue = [
            {"id": f"tool:{t.tool_id}", "label": t.name, "short": t.name, "detail": t.description}
            for t in ex.tool_registry.list_tools()
        ]
        matrix = [
            {"agent_id": a.agent_id, "name": a.name, "department_name": a.department,
             "executes": a.agent_id in _STAGE_BY_AGENT,
             "permissions": [f"tool:{t}" for t in a.tools]}
            for a in rt._agent_definitions()
        ]
        audit_ok = ex.audit_store.is_available
        security = [
            {"control": "API authentication",
             "status": "enforced" if enforced else "not_implemented",
             "detail": "Local accounts with expiring, revocable sessions; failed sign-ins lock the account briefly."
                       if enforced else
                       "No API authentication is enabled; any caller that can reach the service can use it."},
            {"control": "Role-based access control",
             "status": "enforced" if enforced else "not_implemented",
             "detail": "Viewer (read), operator (run workflows) and admin (users, governance, audit) roles are checked on every API route."
                       if enforced else "All callers have full access."},
            {"control": "Cross-origin access", "status": "permissive",
             "detail": "Cross-origin requests are accepted from any origin."},
            {"control": "Human approval gate", "status": "not_implemented",
             "detail": "Deliverables are released automatically once quality review passes."},
            {"control": "Tool allowlist", "status": "enforced",
             "detail": "Tools run only through the ToolRegistry: registered, enabled and input-validated."},
            {"control": "n8n webhook allowlist", "status": "enforced",
             "detail": "Only registered, enabled workflows can be triggered; URLs are never model-supplied."},
            {"control": "Audit trail with redaction", "status": "enforced" if audit_ok else "not_configured",
             "detail": "Append-only; secrets, prompts, responses and webhook URLs are never stored."},
        ]
        denials = sum(1 for e in ex.audit_store.recent(500) if e.status in _DENIAL_STATUSES)
        return {
            "policy": {
                "max_rework_cycles": MAX_REWORKS,
                "require_human_approval": False, "api_key_required": enforced,
                "allow_local_fallback": False, "cors_origins": ["*"],
            },
            "permissions": {"catalogue": catalogue, "matrix": matrix},
            "review_gates": [{
                "gate": "Quality review", "owner": "review",
                "checks": "Completeness, internal consistency and unjustified claims against the requirements.",
                "enforcement": f"Up to {MAX_REWORKS} rework cycles, then released with open items noted.",
            }],
            "containment": {
                "authorisation_denials": denials, "contained_failures": summary["failed_workflows"],
                "failed_agent_runs": summary["failed_agent_runs"],
                "failed_workflows": summary["failed_workflows"],
                "escalations": sum(1 for r in rt._all_runs()
                                   if r["status"] == "completed" and rt._final_review_decision(r) == "rework"),
                "agent_runs": summary["agent_runs"],
            },
            "security": security, "audit": audit_entries,
            "provider": {**provider, "fallback_allowed": False},
        }

    @router.get("/api/audit", tags=["platform"], dependencies=guard("admin"))
    def audit(limit: int = Query(100, ge=1, le=500)) -> dict:
        return {"entries": [_audit_entry(e) for e in ex.audit_store.recent(limit)]}

    return router


def mount_frontend(app: FastAPI, frontend_dir: Path = FRONTEND_DIR) -> None:
    """Serve the operator UI same-origin: /assets, /, and /<page>.html."""
    if not (frontend_dir / "assets").is_dir():
        return
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(frontend_dir / "index.html"))

    @app.get("/{page}.html", include_in_schema=False)
    def page(page: str) -> FileResponse:
        target = frontend_dir / f"{page}.html"
        if not re.fullmatch(r"[A-Za-z0-9_-]+", page) or not target.is_file():
            raise HTTPException(404, "Page not found.")
        return FileResponse(str(target))
