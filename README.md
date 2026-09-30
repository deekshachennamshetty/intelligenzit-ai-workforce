# Intelligenzit AI Workforce Platform

A client requirement goes in; a 7-stage LangGraph pipeline of specialist
agents works it end to end — orchestration, research, requirements,
analysis, solution, quality review (with a capped rework loop), and a final
report — and a structured deliverable comes out. An AI Executive layer plans
which agents a given objective needs, draws on persistent memory and an
ingested knowledge base while planning, and every decision is recorded to an
append-only audit log. A FastAPI backend serves a multi-page operator UI,
protected by local authentication and role-based access control.

The platform is organisation-agnostic: nothing in the pipeline, the agent
roster, or the UI assumes a specific company, industry, or dataset.

## Architecture

```
                    ┌───────────────────────────┐
   objective  ───▶  │       AI Executive        │
                    │  (backend/ai_executive.py)│
                    └─────────────┬─────────────┘
                                  │ plans agent selection via an LLM call,
                                  │ using AgentRegistry + retrieved context
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
      AgentRegistry         MemoryStore          KnowledgeBase
   (backend/agent_          (past engagement    (ingested docs,
    registry.py)             reports, recalled    keyword search —
                              by keyword overlap)  "RAG" layer)
              │
              ▼
   ┌─────────────────────────────────────────────────────────┐
   │      LangGraph pipeline (backend/graph.py, unchanged)     │
   │ Orchestrate → Research → Requirements → Analysis →        │
   │ Solution → Quality Review ──(rework, capped)──▶ Solution   │
   │                     │                                      │
   │                     ▼ (approved)                            │
   │                  Report                                     │
   └─────────────────────────────────────────────────────────┘
              │
              ▼
        RunStore (backend/run_store.py) — persisted workflow
        history, status and metrics, independent of the above

   Supporting, independently-persisted layers used alongside the pipeline:
     ToolRegistry  (backend/tool_registry.py) — explicit, sandboxed tools
     N8NRegistry   (backend/n8n.py)           — explicit n8n workflow allowlist
     AuditStore    (backend/audit.py)         — append-only, redacted audit log
     AuthService   (backend/auth.py)          — local accounts, sessions, RBAC
```

Every agent call goes through `backend/llm.py`, which selects a provider
(Ollama by default, or Anthropic) and supports `MOCK_MODE` for offline
testing — nothing else in the codebase changes based on the provider.

Each store above is a separate SQLite database under `data/`, each fails
safe independently (a broken store degrades to a no-op rather than crashing
a request), and none of them share state with `backend/state.py`'s
`WorkflowState`, which is purely the in-memory, per-run schema LangGraph
operates on.

## Requirements

- Python 3.11+
- One LLM provider:
  - [Ollama](https://ollama.com) running locally (default, no API key needed), **or**
  - An Anthropic API key

## Ollama setup (default provider)

```bash
# 1. Install Ollama: https://ollama.com/download

# 2. Pull the default model
ollama pull llama3.1

# 3. Confirm it's serving (Ollama normally starts this automatically)
curl http://localhost:11434/api/tags
```

No further configuration is needed — `LLM_PROVIDER=ollama` with
`OLLAMA_MODEL=llama3.1` is the default. Real end-to-end runs on
Ollama/llama3.1 are CPU-bound and slow (each of the 7 stages is its own LLM
call; a full engagement can take tens of minutes on modest hardware) —
`MOCK_MODE` (see below) exists precisely so development and tests don't have
to wait on that.

## Environment variables

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `anthropic` |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address |
| `OLLAMA_MODEL` | `llama3.1` | Model to call |
| `ANTHROPIC_API_KEY` | — | Required only if `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model to call when using Anthropic |
| `MOCK_MODE` | `0` | `1` bypasses the LLM entirely with canned responses (used by the test suite; not for normal operation) |
| `INTELLIGENZIT_ADMIN_USERNAME` / `INTELLIGENZIT_ADMIN_PASSWORD` | — | If both are set and no users exist yet, this administrator is created at startup |
| `INTELLIGENZIT_SESSION_HOURS` | `12` | Session token lifetime |
| `INTELLIGENZIT_AUTH_DISABLED` | unset | `1` disables auth enforcement entirely — local development only, never in a shared deployment |

## Installation & startup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env: for Ollama, the defaults already work once it's running;
# for Anthropic, set ANTHROPIC_API_KEY and LLM_PROVIDER=anthropic

# 3. Start the backend (serves both the API and the frontend, same origin)
export $(cat .env | xargs)      # or use your shell's usual way to load .env
uvicorn backend.main:app --reload --port 8000

# 4. Open the UI
# http://localhost:8000/
```

`backend/main.py` mounts the frontend directly (`/`, `/<page>.html`,
`/assets/...`) alongside the `/api/*` adapter — there is no separate build
step or dev server for the frontend.

## First admin creation

On first startup, if `data/auth.sqlite3` has no users yet:

- **Automatic:** set `INTELLIGENZIT_ADMIN_USERNAME` and
  `INTELLIGENZIT_ADMIN_PASSWORD` before starting the server, and that
  administrator is created for you.
- **Manual (CLI):**
  ```bash
  python -m backend.auth create-user --username alice --role admin
  ```
  Prompts for a password (hidden), or reads one from an environment
  variable via `--password-env SOME_VAR`. Other commands:
  ```bash
  python -m backend.auth list-users
  python -m backend.auth reset-password --username alice
  ```

Once at least one admin exists, further users can also be created from the
UI's **Users** page.

## Login & RBAC

Accounts are entirely local — there is no external identity provider.
Three roles, each including everything the previous one can do:

| Role | Can do |
|---|---|
| `viewer` | Read workflows, agents, memory, metrics, dashboard |
| `operator` | Everything `viewer` can, plus submit/run workflows and record decisions |
| `admin` | Everything `operator` can, plus manage users, and view governance/audit |

Signing in at `/login.html` returns a bearer session token (stored in the
browser and sent as `Authorization: Bearer <token>`); every `/api/*` route
except `/api/auth/login` and `/api/auth/status` requires one, at the role
each route declares. `/health` and the static frontend pages themselves are
always reachable without a session — the frontend's own JavaScript is what
redirects an unauthenticated visitor from a protected page to `/login.html`;
the public landing page (`/`) is exempt from that redirect and renders for
anonymous visitors.

Passwords are hashed with scrypt; sessions are opaque bearer tokens (only
their SHA-256 hash is stored); repeated failed logins lock an account
briefly; and every authentication/administration event is written to the
audit log (never passwords or tokens).

## Workflow execution

From **New workflow**, submit a business objective (optionally with a
priority, department, expected outcome, and constraints). The AI Executive
selects which of the registered agents the objective needs, then the
LangGraph pipeline runs them in sequence:

**Orchestration → Research → Requirements → Analysis → Solution → Quality
Review → Report**

Quality Review can send the draft back to Solution for rework, up to a
capped number of cycles, before the report is finalised regardless. Progress
streams live to the **Workflow** page (per-stage status, timing, and the
full activity/event log); the finished result — including the deliverable
text — is available from the **Deliverable** page and persists in RunStore
for the **Workflows** list, **Dashboard**, and **Metrics** pages.

## Memory & RAG

Two independent, separately-persisted retrieval layers feed the AI
Executive's planning step (both are optional context — a workflow runs
normally with neither):

- **Memory** (`backend/memory.py`, `data/memory.sqlite3`) — every completed
  engagement's final report is written here automatically. The next
  objective is matched against this corpus by keyword overlap before
  planning, so relevant past work is recalled as context. Browsable from the
  **Memory** page.
- **Knowledge Base** (`backend/knowledge_base.py`, `data/knowledge_base.sqlite3`)
  — a local document store (`.txt`, `.md`, `.pdf`) that's chunked and
  searched the same way (deterministic keyword overlap, not embeddings).
  There is no ingestion UI or CLI yet; documents are added programmatically:
  ```python
  from backend.knowledge_base import default_knowledge_base
  default_knowledge_base.ingest("path/to/document.md")
  ```

Both retrievals — which entries were used, from which source — are recorded
on the workflow itself and visible on its **Overview** tab.

## Tools & n8n

- **ToolRegistry** (`backend/tool_registry.py`) is the single, fail-safe
  entry point for running a registered tool (a calculator ships as the
  reference example — no `eval`, parsed to an AST and evaluated against an
  explicit operator allow-list). Tools must be explicitly registered and
  explicitly assigned to an agent; the AI Executive validates any tool an
  LLM requests during planning against that assignment.
  **Current limitation:** the AI Executive validates and surfaces tool
  assignments during planning, but nothing in the pipeline itself calls
  `ToolRegistry.execute()` — there is no workflow-level tool execution path
  yet. Tools are exercised directly through `ToolRegistry.execute()` (see
  `tests/test_tool_registry.py`), not automatically mid-workflow.
- **N8NRegistry** (`backend/n8n.py`) extends the same pattern for n8n
  webhooks: workflows are registered in code ahead of time (an explicit
  allowlist), never supplied by an LLM, and n8n workflow definitions store
  only the *name* of an environment variable holding an optional bearer
  token, never the value. No workflows are registered by default.

Both are visible on the **Governance** page (which tools/workflows are
registered and enabled), and every execution is recorded to the audit log.

## Audit & Governance

`backend/audit.py` is an append-only log (no update, no delete) of every
planning decision, dispatch, tool execution, n8n call, and
authentication/administration event. Metadata is redacted before being
stored — raw prompts, LLM responses, secrets, and webhook URLs are never
persisted. The **Governance** page (admin only) surfaces which controls are
actually enforced by the running code (not aspirational ones), the current
LLM provider status, review-gate policy, and the audit log itself;
`/api/audit` and `/api/governance` back it.

## RunStore

`backend/run_store.py` (`data/runs.sqlite3`) is the persistence layer behind
workflow status, results, and metrics — independent of Memory and Audit. It
is what makes engagement history, the Dashboard's aggregate metrics, and the
Metrics page survive a server restart; nothing about a completed workflow
lives only in memory.

## Frontend pages

`agents`, `dashboard`, `deliverable`, `governance`, `memory`, `metrics`,
`new-workflow`, `research`, `settings`, `users`, `workflow`, `workflows` —
the current, actively-maintained operator UI, served same-origin from
`frontend/`. `index.html` is the public landing page.

`legacy-mvp.html` is the original single-page MVP UI, preserved for
historical reference. It predates authentication and calls the legacy
`/submit` / `/status/{id}` / `/result/{id}` routes directly (bypassing the
AI Executive, RunStore, Memory, and Audit entirely) — with RBAC enabled
(the default), it will receive `401 Unauthorized`, since it sends no session
token. It is not part of the supported UI; use the current frontend above
for anything but historical reference.

## Testing

```bash
pytest
```

The suite runs entirely in `MOCK_MODE` (no live provider, no network) and
against isolated, temporary SQLite databases — `tests/conftest.py`
redirects every store's default singleton to a per-test-run temp path
before any test can import the app, so running the suite never writes to
the real databases under `data/`.

## Project structure

```
backend/
  state.py             WorkflowState — the shared per-run state schema
  llm.py               Single LLM provider wrapper (Ollama/Anthropic/MOCK_MODE)
  agents.py             One function per pipeline stage + rework routing
  graph.py              LangGraph wiring: nodes, edges, rework loop
  agent_registry.py     AgentRegistry — the roster of specialist agents
  ai_executive.py        AI Executive — plans agent selection, retrieves
                          memory/knowledge context, dispatches the graph
  memory.py              MemoryStore — persistent, recalled execution memory
  knowledge_base.py      KnowledgeBase — ingested documents, keyword search
  tool_registry.py       ToolRegistry — explicit, sandboxed tool execution
  n8n.py                 N8NRegistry — explicit n8n workflow allowlist
  audit.py               AuditStore — append-only, redacted audit log
  run_store.py           RunStore — persistent workflow history & metrics
  auth.py                AuthService — local accounts, sessions, RBAC, CLI
  auth_api.py            /api/auth/* and /api/users routes
  api_adapter.py         /api/* routes (routes through AIExecutive) + serves the frontend
  main.py                FastAPI app: legacy /submit,/status,/result,/history
                          routes, mounts the /api/* adapter and the frontend
frontend/
  index.html, login.html, dashboard.html, ...   Current operator UI (multi-page)
  legacy-mvp.html                                Preserved original MVP UI (see above)
  assets/                                        Shared JS/CSS for the current UI
tests/
  test_*.py              Full suite (backend + adapter + auth + RBAC + frontend
                          static-serving checks), isolated from real data/
```
