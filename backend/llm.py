"""
Single point of contact with the LLM. Every agent goes through call_llm()
so that model choice, retries, and logging live in one place (Section 14:
"agent actions ... should be logged").

Provider is selected via LLM_PROVIDER ("ollama" by default, or "anthropic"),
with per-provider host/model overrides via OLLAMA_HOST, OLLAMA_MODEL, and
ANTHROPIC_MODEL. MOCK_MODE=1 (env var) bypasses any real LLM call entirely
and returns canned responses instead, so the LangGraph workflow (nodes,
edges, the real review->rework->review conditional routing) can be demoed
without a live provider. Nothing outside this file changes: agents.py,
graph.py, main.py, and the frontend all call the same two functions no
matter which provider or mode is active.
"""
import os
import json
import itertools

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

_anthropic_client = None


def _mock_mode() -> bool:
    return os.environ.get("MOCK_MODE", "0") == "1"


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "ollama").strip().lower()


def _default_model(provider: str) -> str:
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    return os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key, "
                "or set MOCK_MODE=1 to run without one."
            )
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def _call_anthropic(system: str, user: str, model: str, max_tokens: int) -> str:
    client = _get_anthropic_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_ollama(system: str, user: str, model: str, max_tokens: int, json_mode: bool = False) -> str:
    import ollama
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    client = ollama.Client(host=host)
    resp = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"num_predict": max_tokens},
        format="json" if json_mode else None,
    )
    return resp["message"]["content"]


def _dispatch(system: str, user: str, model: str, max_tokens: int, json_mode: bool = False) -> str:
    provider = _provider()
    if provider == "anthropic":
        return _call_anthropic(system, user, model, max_tokens)
    if provider == "ollama":
        return _call_ollama(system, user, model, max_tokens, json_mode=json_mode)
    raise RuntimeError(f"Unknown LLM_PROVIDER '{provider}'. Use 'ollama' or 'anthropic'.")


def call_llm(system: str, user: str, model: str = None, max_tokens: int = 1500) -> str:
    if _mock_mode():
        return _mock_text(system, user)
    provider = _provider()
    model = model or _default_model(provider)
    return _dispatch(system, user, model, max_tokens)


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned)


def call_llm_json(system: str, user: str, model: str = None, max_tokens: int = 1500) -> dict:
    """Ask the model for strict JSON and parse it, stripping code fences if present.
    Retries once with a corrective follow-up if the first response fails to parse."""
    if _mock_mode():
        return _mock_json(system, user)

    provider = _provider()
    model = model or _default_model(provider)
    system_json = system + "\n\nRespond with ONLY valid JSON. No markdown fences, no preamble."

    raw = _dispatch(system_json, user, model, max_tokens, json_mode=True)
    try:
        return _parse_json(raw)
    except json.JSONDecodeError:
        retry_user = (
            f"{user}\n\nYour previous response was not valid JSON:\n{raw}\n\n"
            "Reply again with ONLY valid JSON, no markdown fences, no preamble."
        )
        raw_retry = _dispatch(system_json, retry_user, model, max_tokens, json_mode=True)
        try:
            return _parse_json(raw_retry)
        except json.JSONDecodeError:
            # Fail soft: wrap the raw text rather than crash the whole workflow
            return {"_parse_error": True, "raw": raw_retry}


# ---------------------------------------------------------------------------
# Mock responses. Routed by looking at which agent's system prompt called us
# (each agent's prompt has a distinct, stable opening phrase in agents.py).
# ---------------------------------------------------------------------------

# Tracks how many times the Review agent has been asked, so the mock can
# force one real rework loop (reject first pass, approve second) instead of
# just always approving — this is what makes Review -> Rework -> Review
# visibly demonstrable without a live model.
_review_call_count = itertools.count(1)


def _mock_text(system: str, user: str) -> str:
    if "Research agent" in system:
        return (
            "MOCK RESEARCH FINDINGS\n"
            "- Comparable mid-size logistics/enterprise clients typically run legacy "
            "on-prem inventory systems tightly coupled to SAP via custom middleware.\n"
            "- Common constraints: limited downtime windows, existing SAP licensing, "
            "internal team unfamiliar with cloud-native tooling.\n"
            "- Prior art: phased migrations (strangler-fig pattern) outperform big-bang "
            "rewrites for this profile within a 4-month window."
        )
    if "Requirements agent" in system:
        return (
            "MOCK REQUIREMENTS\n"
            "Functional:\n- Real-time inventory sync with SAP\n- Role-based access for "
            "warehouse and finance teams\n- Audit trail for stock adjustments\n"
            "Non-functional:\n- 99.5% uptime during business hours\n- Delivery within 4 months\n"
            "- No disruption to existing SAP instance during rollout"
        )
    if "Analysis agent" in system:
        return (
            "MOCK ANALYSIS\n"
            "Option A: Full replatform to cloud-native inventory system with SAP "
            "connector — highest long-term scalability, highest risk to 4-month deadline.\n"
            "Option B: Strangler-fig migration, wrapping legacy system with new services "
            "incrementally — lower risk, fits timeline, defers full modernisation.\n"
            "Option C: SAP-native extension — fastest, but couples the client further to SAP.\n"
            "Key risk across all options: data migration integrity during cutover."
        )
    if "Solution agent" in system:
        rework_note = ""
        if "previous draft" in user.lower():
            rework_note = (
                "\n\nREVISED PER REVIEWER FEEDBACK: added explicit rollback plan and "
                "cutover testing window, as requested."
            )
        return (
            "MOCK SOLUTION\n"
            "Recommend Option B (strangler-fig migration). Stand up new inventory "
            "microservice alongside the legacy system, route new SKU categories to it "
            "first, migrate remaining categories in 3 phased cutovers over the 4-month "
            "window, decommission legacy system last." + rework_note
        )
    if "Report agent" in system:
        return (
            "# Technology Recommendation — Inventory System Modernisation (MOCK REPORT)\n\n"
            "## Requirements\n(see structured requirements above)\n\n"
            "## Analysis\nThree options evaluated; strangler-fig migration selected for "
            "best fit against the 4-month constraint and SAP integration risk.\n\n"
            "## Recommended Solution\nPhased strangler-fig migration, 3 cutover phases, "
            "rollback plan and dedicated cutover testing window included per review.\n\n"
            "## Status\nApproved after one rework cycle (reviewer requested rollback plan)."
        )
    return "MOCK RESPONSE: no matching agent prompt pattern found."


def _mock_json(system: str, user: str) -> dict:
    if "Orchestrator agent" in system:
        return {
            "request_type": "technology_recommendation",
            "execution_plan": ["research", "requirements", "analysis", "solution"],
        }
    if "Review/Critic agent" in system:
        call_number = next(_review_call_count)
        if call_number == 1:
            return {
                "decision": "rework",
                "notes": "Solution is missing a rollback plan and doesn't mention how "
                         "cutover will be tested before go-live. Please add both.",
            }
        return {
            "decision": "approve",
            "notes": "Rollback plan and cutover testing window now addressed. Approved.",
        }
    return {"_parse_error": True, "raw": "MOCK: no matching agent prompt pattern found."}
