from langgraph.graph import StateGraph, END
from .state import WorkflowState
from . import agents


def build_graph():
    g = StateGraph(WorkflowState)

    g.add_node("orchestrate", agents.orchestrator_node)
    g.add_node("research", agents.research_node)
    g.add_node("requirements", agents.requirements_node)
    g.add_node("analysis", agents.analysis_node)
    g.add_node("solution", agents.solution_node)
    g.add_node("review", agents.review_node)
    g.add_node("report", agents.report_node)

    g.set_entry_point("orchestrate")
    g.add_edge("orchestrate", "research")
    g.add_edge("research", "requirements")
    g.add_edge("requirements", "analysis")
    g.add_edge("analysis", "solution")
    g.add_edge("solution", "review")

    # Section 7: Review issues pass/rework. Rework loops back to Solution,
    # capped at MAX_REWORKS (see agents.route_after_review) to satisfy
    # Section 14's "safe stopping" requirement.
    g.add_conditional_edges(
        "review",
        agents.route_after_review,
        {"rework": "solution", "approve": "report"},
    )
    g.add_edge("report", END)

    return g.compile()


workflow_graph = build_graph()
