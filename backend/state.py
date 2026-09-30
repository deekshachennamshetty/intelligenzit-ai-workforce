"""
Shared state passed between every node in the LangGraph workflow.
This is the single source of truth Section 8 of the proposal refers to as
"Shared state managed through LangGraph so agents can use earlier results."
"""
from typing import TypedDict, List, Optional


class LogEntry(TypedDict):
    stage: str
    summary: str


class WorkflowState(TypedDict, total=False):
    # Input
    request_id: str
    raw_request: str

    # Stage 2: Orchestrator
    request_type: str
    execution_plan: List[str]

    # Stage 3: Research
    research_findings: str

    # Stage 4: Requirements
    requirements: str

    # Stage 5: Analysis
    analysis: str

    # Stage 6: Solution
    solution: str

    # Stage 7: Review / Critic
    review_decision: str  # "approve" | "rework"
    review_notes: str
    rework_count: int
    rework_target: Optional[str]  # which stage to send back to

    # Stage 8: Report
    final_report: str

    # Meta
    status: str  # "running" | "complete" | "failed"
    log: List[LogEntry]
