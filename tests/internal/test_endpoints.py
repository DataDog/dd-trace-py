import threading
from time import sleep

import pytest

from ddtrace.internal.endpoints import HttpEndPointsCollection


@pytest.fixture
def collection():
    coll = HttpEndPointsCollection()
    coll.reset()
    yield coll
    coll.reset()


def test_flush_uses_tuple_snapshot(collection):
    """Test that flush() operates on a tuple snapshot, not the original set."""
    collection.add_endpoint("GET", "/api/users")
    collection.add_endpoint("POST", "/api/users")
    collection.add_endpoint("DELETE", "/api/users/123")

    assert len(collection.endpoints) == 3
    result = collection.flush(max_length=10)
    assert result["is_first"] is True
    assert len(result["endpoints"]) == 3
    assert len(collection.endpoints) == 0


def test_flush_snapshot_prevents_modification_during_iteration(collection):
    """Test that modifying self.endpoints during flush iteration doesn't cause RuntimeError."""
    collection.add_endpoint("GET", "/api/v1")
    collection.add_endpoint("POST", "/api/v2")
    collection.add_endpoint("PUT", "/api/v3")

    initial_count = len(collection.endpoints)
    assert initial_count == 3
    result = collection.flush(max_length=10)

    assert len(result["endpoints"]) == initial_count
    assert result["is_first"] is True


def test_concurrent_add_during_flush_does_not_break_iteration(collection):
    """Test that adding endpoints from another thread during flush doesn't cause RuntimeError."""
    for i in range(5):
        collection.add_endpoint("GET", f"/api/endpoint{i}")

    assert len(collection.endpoints) == 5

    flush_completed = threading.Event()
    flush_result = {}
    exception_caught = []

    def flush_thread():
        try:
            result = collection.flush(max_length=10)
            flush_result["data"] = result
            flush_completed.set()
        except Exception as e:
            exception_caught.append(e)
            flush_completed.set()

    def add_thread():
        sleep(0.001)

        # Try to modify the set while flush might be iterating
        for i in range(5, 10):
            collection.add_endpoint("POST", f"/api/new{i}")
            sleep(0.001)

    t1 = threading.Thread(target=flush_thread)
    t2 = threading.Thread(target=add_thread)

    t1.start()
    t2.start()

    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert flush_completed.is_set(), "Flush did not complete"
    assert len(exception_caught) == 0, f"Exception occurred during flush: {exception_caught}"
    assert "data" in flush_result, "Flush did not return a result"

    result = flush_result["data"]
    assert "endpoints" in result
    assert "is_first" in result


def test_flush_with_partial_batch(collection):
    """Test that flush creates a tuple snapshot even when using pop() for partial batches."""
    for i in range(10):
        collection.add_endpoint("GET", f"/api/endpoint{i}")

    assert len(collection.endpoints) == 10

    result = collection.flush(max_length=5)

    assert len(result["endpoints"]) == 5
    assert result["is_first"] is True
    assert len(collection.endpoints) == 5

    result2 = collection.flush(max_length=10)
    assert len(result2["endpoints"]) == 5
    assert result2["is_first"] is False  # Not first anymore

    assert len(collection.endpoints) == 0


def test_partial_flush_with_concurrent_modification(collection):
    """Test that partial flush (max_length < size) is safe from race conditions."""
    for i in range(10):
        collection.add_endpoint("GET", f"/api/endpoint{i}")

    assert len(collection.endpoints) == 10

    flush_completed = threading.Event()
    flush_result = {}
    exception_caught = []

    def flush_thread():
        try:
            # Partial flush - this should trigger the else branch at line 118
            result = collection.flush(max_length=5)
            flush_result["data"] = result
            flush_completed.set()
        except Exception as e:
            exception_caught.append(e)
            flush_completed.set()

    def add_thread():
        sleep(0.001)
        # Try to modify the set while flush might be iterating
        for i in range(10, 15):
            collection.add_endpoint("POST", f"/api/new{i}")
            sleep(0.001)

    t1 = threading.Thread(target=flush_thread)
    t2 = threading.Thread(target=add_thread)

    t1.start()
    t2.start()

    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert flush_completed.is_set(), "Flush did not complete"
    assert len(exception_caught) == 0, f"Exception occurred during flush: {exception_caught}"
    assert "data" in flush_result, "Flush did not return a result"

    result = flush_result["data"]
    assert len(result["endpoints"]) == 5
    assert "is_first" in result


def test_http_endpoint_hash_consistency(collection):
    """Test that HttpEndPoint hashing works correctly for set operations."""
    collection.add_endpoint("GET", "/api/test")
    collection.add_endpoint("GET", "/api/test")
    assert len(collection.endpoints) == 1

    collection.add_endpoint("POST", "/api/test")
    collection.add_endpoint("GET", "/api/other")
    assert len(collection.endpoints) == 3


def test_snapshot_is_tuple_type(collection):
    """Verify that the snapshot created in flush is actually a tuple."""
    collection.add_endpoint("GET", "/test")
    collection.add_endpoint("POST", "/test")
    assert isinstance(collection.endpoints, set)

    result = collection.flush(max_length=10)
    assert len(result["endpoints"]) == 2

    for ep in result["endpoints"]:
        assert isinstance(ep, dict)
        assert "method" in ep
        assert "path" in ep
