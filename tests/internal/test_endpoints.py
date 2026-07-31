from time import monotonic
from unittest import mock

import pytest

from ddtrace.internal.endpoints import HttpEndPoint
from ddtrace.internal.endpoints import HttpEndPointsCollection


@pytest.fixture
def collection():
    # ``HttpEndPointsCollection`` is a singleton; reset its mutable state around each test.
    coll = HttpEndPointsCollection()
    coll.reset()
    coll.max_size_length = 900
    coll.drop_time_seconds = 90.0
    yield coll
    coll.reset()
    coll.max_size_length = 900
    coll.drop_time_seconds = 90.0


@pytest.fixture(autouse=True)
def record_endpoint(collection):
    """Subscribe a mock in place of the telemetry writer.

    ``add_endpoint`` notifies whatever is registered as ``on_endpoint_registered``; substituting a
    mock lets these unit tests assert on what gets forwarded without spinning up the native worker
    or depending on its state.
    """
    previous = collection.on_endpoint_registered
    m = mock.Mock()
    collection.on_endpoint_registered = m
    yield m
    collection.on_endpoint_registered = previous


def test_add_endpoint_populates_set(collection):
    collection.add_endpoint("GET", "/api/users")
    collection.add_endpoint("POST", "/api/users")
    collection.add_endpoint("DELETE", "/api/users/123")

    assert len(collection.endpoints) == 3


def test_add_endpoint_forwards_normalized_fields(collection, record_endpoint):
    """Each new endpoint is forwarded once, method upper-cased and resource defaulted."""
    collection.add_endpoint("get", "/api/users", operation_name="flask.request")

    record_endpoint.assert_called_once_with(
        HttpEndPoint(method="GET", path="/api/users", resource_name="GET /api/users", operation_name="flask.request")
    )


def test_endpoints_are_collected_without_a_subscriber(collection):
    """Registrations before telemetry subscribes are still collected, for it to replay."""
    collection.on_endpoint_registered = None

    collection.add_endpoint("GET", "/api/users")

    assert len(collection.endpoints) == 1


def test_duplicate_endpoint_is_not_forwarded_again(collection, record_endpoint):
    collection.add_endpoint("GET", "/api/test")
    collection.add_endpoint("GET", "/api/test")

    assert len(collection.endpoints) == 1
    assert record_endpoint.call_count == 1


def test_http_endpoint_hash_consistency(collection):
    """HttpEndPoint hashes by (method, path); differing method or path is a distinct entry."""
    collection.add_endpoint("GET", "/api/test")
    collection.add_endpoint("GET", "/api/test")
    assert len(collection.endpoints) == 1

    collection.add_endpoint("POST", "/api/test")
    collection.add_endpoint("GET", "/api/other")
    assert len(collection.endpoints) == 3


def test_explicit_resource_name_is_preserved(collection, record_endpoint):
    collection.add_endpoint("GET", "/api/users/{id}", resource_name="users.show")

    record_endpoint.assert_called_once_with(
        HttpEndPoint(method="GET", path="/api/users/{id}", resource_name="users.show")
    )


def test_max_size_cap_stops_registration(collection, record_endpoint):
    collection.max_size_length = 3
    for i in range(10):
        collection.add_endpoint("GET", f"/api/endpoint{i}")

    assert len(collection.endpoints) == 3
    assert record_endpoint.call_count == 3


def test_drop_time_resets_stale_collection(collection, record_endpoint):
    collection.add_endpoint("GET", "/api/old")
    assert len(collection.endpoints) == 1

    # Simulate a long idle gap (e.g. a dev-server hot reload): the next registration should
    # drop the stale route table before adding the new endpoint.
    collection.last_modification_time = monotonic() - collection.drop_time_seconds - 1
    collection.add_endpoint("GET", "/api/new")

    assert len(collection.endpoints) == 1
    assert next(iter(collection.endpoints)).path == "/api/new"


def test_reset_clears_endpoints(collection):
    collection.add_endpoint("GET", "/api/a")
    collection.add_endpoint("POST", "/api/b")
    assert len(collection.endpoints) == 2

    collection.reset()
    assert len(collection.endpoints) == 0


def test_http_endpoint_defaults_resource_name():
    ep = HttpEndPoint(method="get", path="/x")
    # method upper-cased, resource_name defaulted to "<METHOD> <path>"
    assert ep.method == "GET"
    assert ep.resource_name == "GET /x"
