"""
Section 7 agent contracts, implemented as LangGraph nodes.
Each function: reads only the state keys it needs (least privilege, Section 14),
writes only its own output keys, and appends one log entry.
"""
from .state import WorkflowState
from .llm import call_llm, call_llm_json

MAX_REWORKS = 2  # Section 7: Review/Critic issues pass/rework; cap prevents infinite loops


def _log(state: WorkflowState, stage: str, summary: str):
    state.setdefault("log", []).append({"stage": stage, "summary": summary})


# ---- Stage 2: Orchestrator ----------------------------------------------
def orchestrator_node(state: WorkflowState) -> WorkflowState:
    result = call_llm_json(
        system=(
            "You are the Orchestrator agent for an internal AI workforce platform. "
            "Classify the incoming business/client requirement and produce a short "
            "execution plan naming the stages needed: research, requirements, "
            "analysis, solution. Almost all requests need all four; only skip a "
            "stage if it is clearly irrelevant."
        ),
        user=f"Requirement:\n{state['raw_request']}\n\n"
             'Return JSON: {"request_type": str, "execution_plan": [str, ...]}',
    )
    state["request_type"] = result.get("request_type", "technology_recommendation")
    state["execution_plan"] = result.get(
        "execution_plan", ["research", "requirements", "analysis", "solution"]
    )
    state["rework_count"] = 0
    _log(state, "orchestrate", f"Classified as '{state['request_type']}'")
    return state


# ---- Stage 3: Research ----------------------------------------------------
def research_node(state: WorkflowState) -> WorkflowState:
    findings = call_llm(
        system=(
            "You are the Research agent. Gather and organise relevant background "
            "information, prior art, and constraints for the requirement below. "
            "Be concrete. Do not propose a solution yet — that is a later stage's job."
        ),
        user=f"Requirement:\n{state['raw_request']}",
    )
    state["research_findings"] = findings
    _log(state, "research", "Findings gathered")
    return state


# ---- Stage 4: Requirements -------------------------------------------------
def requirements_node(state: WorkflowState) -> WorkflowState:
    reqs = call_llm(
        system=(
            "You are the Requirements agent. Convert the raw request and research "
            "findings into a clear, structured list of business and technical "
            "requirements (functional and non-functional). Use bullet points."
        ),
        user=f"Requirement:\n{state['raw_request']}\n\n"
             f"Research findings:\n{state.get('research_findings','')}",
    )
    state["requirements"] = reqs
    _log(state, "requirements", "Requirements structured")
    return state


# ---- Stage 5: Analysis -----------------------------------------------------
def analysis_node(state: WorkflowState) -> WorkflowState:
    analysis = call_llm(
        system=(
            "You are the Analysis agent. Given the requirements, identify viable "
            "options, trade-offs, risks, and constraints. Do not pick a final "
            "answer yet — lay out the decision space clearly."
        ),
        user=f"Requirements:\n{state.get('requirements','')}\n\n"
             f"Research findings:\n{state.get('research_findings','')}",
    )
    state["analysis"] = analysis
    _log(state, "analysis", "Options and risks identified")
    return state


# ---- Stage 6: Solution ------------------------------------------------------
def solution_node(state: WorkflowState) -> WorkflowState:
    rework_context = ""
    if state.get("review_notes"):
        rework_context = (
            f"\n\nA previous draft was sent back for rework. Reviewer notes:\n"
            f"{state['review_notes']}\nAddress these directly."
        )
    solution = call_llm(
        system=(
            "You are the Solution agent. Propose a specific, justified technical "
            "recommendation based on the analysis. Be decisive — pick an approach "
            "and defend it."
        ),
        user=f"Analysis:\n{state.get('analysis','')}{rework_context}",
    )
    state["solution"] = solution
    _log(state, "solution", f"Draft produced (attempt {state.get('rework_count', 0) + 1})")
    return state


# ---- Stage 7: Review / Critic ----------------------------------------------
def review_node(state: WorkflowState) -> WorkflowState:
    result = call_llm_json(
        system=(
            "You are the Review/Critic agent. Check the proposed solution against "
            "the original requirements for completeness, internal consistency, and "
            "obvious gaps or unjustified claims. Be strict but fair."
        ),
        user=f"Requirements:\n{state.get('requirements','')}\n\n"
             f"Proposed solution:\n{state.get('solution','')}\n\n"
             'Return JSON: {"decision": "approve" or "rework", "notes": str}',
    )
    state["review_decision"] = result.get("decision", "approve")
    state["review_notes"] = result.get("notes", "")
    _log(state, "review", f"Decision: {state['review_decision']}")
    return state


def route_after_review(state: WorkflowState) -> str:
    """Conditional edge: approve -> report, rework -> back to solution (capped)."""
    if state.get("review_decision") == "rework" and state.get("rework_count", 0) < MAX_REWORKS:
        state["rework_count"] = state.get("rework_count", 0) + 1
        return "rework"
    if state.get("review_decision") == "rework":
        # Hit the cap: escalate rather than loop forever (Section 14: safe stopping)
        _log(state, "review", f"Rework cap ({MAX_REWORKS}) reached — escalating as-is")
    return "approve"


# ---- Stage 8: Report ---------------------------------------------------------
def report_node(state: WorkflowState) -> WorkflowState:
    report = call_llm(
        system=(
            "You are the Report agent. Produce a final, client-ready deliverable "
            "combining the requirements, analysis, and approved solution into a "
            "clean structured memo with headings. If the reviewer left open notes "
            "because a rework cap was hit, add a short 'Open Items' section."
        ),
        user=f"Requirements:\n{state.get('requirements','')}\n\n"
             f"Analysis:\n{state.get('analysis','')}\n\n"
             f"Solution:\n{state.get('solution','')}\n\n"
             f"Reviewer notes:\n{state.get('review_notes','')}",
    )
    state["final_report"] = report
    state["status"] = "complete"
    _log(state, "report", "Final deliverable produced")
    return state
