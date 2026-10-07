import json
from dataclasses import replace

import pytest

from src.agent.config import AgentConfig
from src.agent.errors import AgentError
from src.agent.store import JobStore, WorkerLease
from tests.agent.test_tools_and_result import snapshot


def setup_store(tmp_path, **kwargs):
    config = AgentConfig(database=str(tmp_path / "jobs.sqlite"), **kwargs)
    clock = [1000.]
    return JobStore(config.database, config, clock=lambda: clock[0]), clock


def test_shared_cache_preserves_requesters_separately_from_ownership(tmp_path):
    store, _ = setup_store(tmp_path)
    first, cached = store.enqueue(snapshot(), "same", "dispatcher-1")
    second, cached = store.enqueue(snapshot(), "same", "dispatcher-3")
    assert cached and first["job_id"] == second["job_id"]
    assert second["requested_by_operator_ids"] == ["dispatcher-1", "dispatcher-3"]
    assert second["requested_by_operator_id"] == "dispatcher-1"
    third, _ = store.enqueue(snapshot(), "other", "dispatcher-3")
    fourth, _ = store.enqueue(snapshot(), "other", "dispatcher-1")
    assert fourth["requested_by_operator_id"] == "dispatcher-3"


def test_one_running_two_queued_fifo_and_capacity(tmp_path):
    store, clock = setup_store(tmp_path)
    first, _ = store.enqueue(snapshot(), "first", "dispatcher-1")
    assert store.take_next()[0] == first["job_id"]
    clock[0] += 1
    second, _ = store.enqueue(snapshot(), "second", "dispatcher-2")
    third, _ = store.enqueue(snapshot(), "third", "dispatcher-3")
    with pytest.raises(AgentError) as exc:
        store.enqueue(snapshot(), "fourth", "dispatcher-1")
    assert exc.value.code == "queue_full" and exc.value.status == 429
    assert store.take_next() is None
    store.finish(first["job_id"], result={"summary": "verified test"})
    assert store.take_next()[0] == second["job_id"]
    store.finish(second["job_id"], result={"summary": "verified test"})
    assert store.take_next()[0] == third["job_id"]


def test_admission_wait_must_be_strictly_less_than_budget(tmp_path):
    store, _ = setup_store(tmp_path)
    first, _ = store.enqueue(snapshot(), "first", "dispatcher-1")
    store.take_next()
    store.enqueue(snapshot(), "second", "dispatcher-2")
    with pytest.raises(AgentError) as exc:
        store.enqueue(snapshot(), "third", "dispatcher-3")
    assert exc.value.code == "queue_busy"  # 60 + 60 == 120, not strictly less.


def test_restart_interrupts_running_and_keeps_original_queue_deadline(tmp_path):
    store, clock = setup_store(tmp_path)
    first, _ = store.enqueue(snapshot(), "first", "dispatcher-1")
    store.take_next()
    clock[0] += 5
    second, _ = store.enqueue(snapshot(), "second", "dispatcher-2")
    clock[0] += 90
    store.recover()
    assert store.get(first["job_id"])["error"]["code"] == "interrupted"
    assert store.get(second["job_id"])["queued_at"] == second["queued_at"]
    clock[0] += 31
    assert store.get(second["job_id"])["error"]["code"] == "queue_timeout"
    retry, cached = store.enqueue(snapshot(), "first", "dispatcher-1")
    assert not cached and retry["job_id"] != first["job_id"]


def test_worker_lease_rejects_second_process_and_releases(tmp_path):
    first, second = WorkerLease(tmp_path / "jobs.sqlite"), WorkerLease(tmp_path / "jobs.sqlite")
    first.acquire()
    try:
        with pytest.raises(AgentError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
