"""
Tests for backend/memory.py: persistence, search ranking, deletion,
restart behavior (a fresh MemoryStore pointed at the same db file sees
prior data), and fail-safe degradation when the database is unavailable.
"""
import pytest

from backend.memory import MemoryRecord, MemoryStore, default_memory


def make_store(tmp_path, name="mem.sqlite3") -> MemoryStore:
    return MemoryStore(db_path=tmp_path / name)


# --- store / get -----------------------------------------------------------

def test_store_returns_a_memory_record_with_generated_id_and_timestamp(tmp_path):
    store = make_store(tmp_path)
    record = store.store("Task A", "Some content", agent_id="report")

    assert isinstance(record, MemoryRecord)
    assert record.id
    assert record.task == "Task A"
    assert record.content == "Some content"
    assert record.agent_id == "report"
    assert record.created_at
    assert record.metadata == {}


def test_store_and_get_roundtrip(tmp_path):
    store = make_store(tmp_path)
    record = store.store("Task A", "Content A", metadata={"foo": "bar"})

    fetched = store.get(record.id)

    assert fetched == record
    assert fetched.metadata == {"foo": "bar"}


def test_store_generates_unique_ids(tmp_path):
    store = make_store(tmp_path)
    r1 = store.store("t", "c1")
    r2 = store.store("t", "c2")
    assert r1.id != r2.id


def test_get_missing_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get("does-not-exist") is None


# --- delete ------------------------------------------------------------

def test_delete_removes_record(tmp_path):
    store = make_store(tmp_path)
    record = store.store("t", "c")

    assert store.delete(record.id) is True
    assert store.get(record.id) is None


def test_delete_missing_returns_false(tmp_path):
    store = make_store(tmp_path)
    assert store.delete("does-not-exist") is False


# --- search --------------------------------------------------------------

def test_search_finds_matching_record_by_keyword_overlap(tmp_path):
    store = make_store(tmp_path)
    store.store(
        "Modernise inventory system for logistics client",
        "Recommended a strangler-fig migration for SAP integration.",
        agent_id="report",
    )
    store.store("Unrelated churn analysis task", "Something about customer churn.", agent_id="report")

    results = store.search("inventory logistics SAP migration")

    assert len(results) == 1
    assert "inventory" in results[0].task.lower()


def test_search_returns_empty_list_when_nothing_matches(tmp_path):
    store = make_store(tmp_path)
    store.store("Completely different topic", "Nothing relevant here.")
    assert store.search("quantum spaceship unicorn") == []


def test_search_returns_empty_list_for_blank_query(tmp_path):
    store = make_store(tmp_path)
    store.store("t", "c")
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_respects_limit(tmp_path):
    store = make_store(tmp_path)
    for i in range(10):
        store.store(f"inventory task {i}", "inventory system content", agent_id="report")

    results = store.search("inventory", limit=3)
    assert len(results) == 3


def test_search_ranks_higher_overlap_first(tmp_path):
    store = make_store(tmp_path)
    low = store.store("inventory task", "just inventory, nothing else")
    high = store.store("inventory logistics SAP migration task", "inventory logistics SAP migration content")

    results = store.search("inventory logistics SAP migration")

    assert results[0].id == high.id
    assert results[1].id == low.id


def test_search_filters_by_agent_id(tmp_path):
    store = make_store(tmp_path)
    store.store("inventory task", "inventory content", agent_id="report")
    store.store("inventory task", "inventory content", agent_id="research")

    results = store.search("inventory", agent_id="research")

    assert len(results) == 1
    assert results[0].agent_id == "research"


# --- persistence across "restarts" -----------------------------------------

def test_data_persists_across_new_store_instances_pointed_at_same_file(tmp_path):
    db_path = tmp_path / "restart_test.sqlite3"

    store1 = MemoryStore(db_path=db_path)
    record = store1.store("Persisted task", "Persisted content", agent_id="report")
    del store1  # simulate the process ending

    store2 = MemoryStore(db_path=db_path)  # simulate the app restarting
    fetched = store2.get(record.id)
    found = store2.search("Persisted content")

    assert fetched == record
    assert any(r.id == record.id for r in found)


# --- fail-safe behavior ------------------------------------------------

def test_fails_safe_when_db_directory_cannot_be_created(tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("i am a file, not a directory")
    bad_db_path = blocking_file / "sub" / "memory.sqlite3"

    with pytest.warns(RuntimeWarning):
        store = MemoryStore(db_path=bad_db_path)

    assert store.is_available is False
    assert store.store("t", "c") is None
    assert store.search("t") == []
    assert store.get("anything") is None
    assert store.delete("anything") is False


def test_available_store_reports_is_available_true(tmp_path):
    store = make_store(tmp_path)
    assert store.is_available is True


def test_default_memory_singleton_is_a_memory_store():
    assert isinstance(default_memory, MemoryStore)


# --- count() / list_recent() (read-only listing used by the HTTP adapter) ----

def test_count_reflects_stored_records(tmp_path):
    store = make_store(tmp_path)
    assert store.count() == 0
    store.store("a", "one")
    store.store("b", "two")
    assert store.count() == 2


def test_list_recent_is_newest_first_and_respects_limit(tmp_path):
    store = make_store(tmp_path)
    first = store.store("a", "one")
    second = store.store("b", "two")
    third = store.store("c", "three")

    assert [r.id for r in store.list_recent()] == [third.id, second.id, first.id]
    assert [r.id for r in store.list_recent(limit=2)] == [third.id, second.id]


def test_count_and_list_recent_fail_safe_when_unavailable(tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        store = MemoryStore(db_path=blocking_file / "sub" / "mem.sqlite3")
    assert store.count() == 0
    assert store.list_recent() == []
