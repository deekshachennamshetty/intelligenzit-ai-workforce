"""
End-to-end test of the LangGraph workflow in MOCK_MODE — no live LLM
provider required, with real assertions on the graph wiring and the
review/rework routing.
"""
import pytest

from backend.graph import workflow_graph


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def test_workflow_completes_with_one_rework_cycle():
    result = workflow_graph.invoke({
        "request_id": "test-pytest",
        "raw_request": (
            "A mid-size logistics client wants to modernise their on-prem "
            "inventory system. Needs to scale, integrate with existing SAP, "
            "deliver in 4 months."
        ),
        "status": "running",
        "log": [],
    })

    assert result["status"] == "complete"
    assert result.get("final_report")

    stages = [entry["stage"] for entry in result["log"]]
    assert stages[0] == "orchestrate"
    assert stages[-1] == "report"
    # Mock Review agent rejects the first draft, approves the second.
    assert stages.count("solution") == 2
    assert stages.count("review") == 2
    assert result["review_decision"] == "approve"
