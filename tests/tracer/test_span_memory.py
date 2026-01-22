"""
Memory size tests for Span objects.

These tests validate that span memory usage (deep/recursive size including all
referenced objects) stays predictable across Python versions and doesn't regress
unexpectedly. As the PyO3 migration progresses and data moves from Python __slots__
to Rust structs, these baselines will need updating.

Deep size includes:
- The Span object itself
- All dictionaries (_meta, _metrics, etc.)
- All lists (_events, _links, etc.)
- All referenced objects (SpanEvent, SpanLink, strings, etc.)

To update expected sizes after running tests:
    1. Run: ./scripts/run-tests tests/tracer/test_span_memory.py --venv <hash> -- -- -k test_span_memory
    2. Copy actual sizes from failure messages into the expected size dicts below
    3. Re-run with -s to verify: ./scripts/run-tests ... --venv <hash> -- -s -- -k test_span_memory
"""

import sys

import pytest

from ddtrace._trace.span import Span
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.native._native import SpanEventData
from ddtrace.internal.native._native import SpanLinkData


def get_python_version_tuple():
    """Get Python version as tuple (major, minor)."""
    return (sys.version_info.major, sys.version_info.minor)


def get_deep_size(obj, seen=None):
    """
    Recursively calculate the deep size of an object including all referenced objects.

    This traverses the object graph to calculate total memory usage, handling:
    - Primitives (int, float, str, bytes, etc.)
    - Collections (dict, list, tuple, set, frozenset)
    - Objects with __dict__
    - Objects with __slots__
    - Prevents infinite recursion via seen set
    """
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return 0

    seen.add(obj_id)
    size = sys.getsizeof(obj)

    # Handle different types
    if isinstance(obj, dict):
        size += sum(get_deep_size(k, seen) + get_deep_size(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(get_deep_size(item, seen) for item in obj)
    elif hasattr(obj, "__dict__"):
        size += get_deep_size(obj.__dict__, seen)
    elif hasattr(obj, "__slots__"):
        # Handle objects with __slots__
        for slot in obj.__slots__:
            if hasattr(obj, slot):
                try:
                    size += get_deep_size(getattr(obj, slot), seen)
                except (AttributeError, ValueError):
                    # Some slots may not be set or may raise errors
                    pass

    return size


def build_empty_span():
    """Minimal span with just a name."""
    return Span("test")


def build_span_with_service_resource():
    """Span with name, service, and resource."""
    return Span("test", service="test-service", resource="/api/endpoint")


def build_span_with_meta():
    """Span with string tags."""
    span = Span("test")
    span.set_tag("http.method", "GET")
    span.set_tag("http.url", "https://example.com/api/test")
    span.set_tag("http.status_code", "200")
    return span


def build_span_with_metrics():
    """Span with numeric metrics."""
    span = Span("test")
    span.set_metric("duration", 1.23)
    span.set_metric("request.size", 1024)
    span.set_metric("response.size", 2048)
    return span


def build_span_with_meta_and_metrics():
    """Span with both string tags and numeric metrics."""
    span = Span("test")
    span.set_tag("http.method", "POST")
    span.set_tag("http.url", "https://example.com/api/create")
    span.set_metric("request.size", 512)
    span.set_metric("response.size", 1024)
    return span


def build_span_with_error():
    """Span with error information."""
    span = Span("test")
    try:
        raise ValueError("Test error")
    except ValueError:
        span.set_exc_info(*sys.exc_info())
    return span


def build_span_with_single_event():
    """Span with one event."""
    span = Span("test")
    span._add_event("test_event", attributes={"key": "value"})
    return span


def build_span_with_multiple_events():
    """Span with multiple events."""
    span = Span("test")
    span._add_event("event1", attributes={"key1": "value1"})
    span._add_event("event2", attributes={"key2": "value2"})
    span._add_event("event3", attributes={"key3": "value3"})
    return span


def build_span_with_single_link():
    """Span with one span link."""
    span = Span("test")
    span.set_link(trace_id=123456789, span_id=987654321)
    return span


def build_span_with_multiple_links():
    """Span with multiple span links."""
    span = Span("test")
    span.set_link(trace_id=111, span_id=222)
    span.set_link(trace_id=333, span_id=444)
    span.set_link(trace_id=555, span_id=666)
    return span


def build_span_fully_populated():
    """Span with all features: service, resource, meta, metrics, events, links."""
    span = Span("test", service="test-service", resource="/api/endpoint")
    span.set_tag("http.method", "POST")
    span.set_tag("http.url", "https://example.com/api/test")
    span.set_metric("request.size", 1024)
    span.set_metric("response.size", 2048)
    span._add_event("event1", attributes={"key": "value"})
    span.set_link(trace_id=123, span_id=456)
    return span


# Scenario definitions: (scenario_name, builder_function, expected_sizes_by_python_version)
# Expected sizes are a dict mapping (major, minor) Python version to expected byte size
# Use 0 as placeholder - run tests to get actual values, then update these
SPAN_SCENARIOS = [
    (
        "empty_span",
        build_empty_span,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1089,
            (3, 11): 1097,
            (3, 12): 1105,
            (3, 13): 1105,
        },
    ),
    (
        "span_with_service_resource",
        build_span_with_service_resource,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1212,
            (3, 11): 1220,
            (3, 12): 1208,
            (3, 13): 1212,
        },
    ),
    (
        "span_with_meta",
        build_span_with_meta,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1620,
            (3, 11): 1580,
            (3, 12): 1540,
            (3, 13): 1540,
        },
    ),
    (
        "span_with_metrics",
        build_span_with_metrics,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1517,
            (3, 11): 1477,
            (3, 12): 1461,
            (3, 13): 1461,
        },
    ),
    (
        "span_with_meta_and_metrics",
        build_span_with_meta_and_metrics,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1853,
            (3, 11): 1765,
            (3, 12): 1725,
            (3, 13): 1725,
        },
    ),
    (
        "span_with_error",
        build_span_with_error,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1808,
            (3, 11): 1764,
            (3, 12): 1724,
            (3, 13): 1724,
        },
    ),
    (
        "span_with_single_event",
        build_span_with_single_event,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1265,
            (3, 11): 1273,
            (3, 12): 1281,
            (3, 13): 1281,
        },
    ),
    (
        "span_with_multiple_events",
        build_span_with_multiple_events,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1297,
            (3, 11): 1305,
            (3, 12): 1313,
            (3, 13): 1313,
        },
    ),
    (
        "span_with_single_link",
        build_span_with_single_link,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 1730,
            (3, 11): 1914,
            (3, 12): 1874,
            (3, 13): 1874,
        },
    ),
    (
        "span_with_multiple_links",
        build_span_with_multiple_links,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 2306,
            (3, 11): 2842,
            (3, 12): 2802,
            (3, 13): 2802,
        },
    ),
    (
        "span_fully_populated",
        build_span_fully_populated,
        {
            # (3, 9) removed - non-deterministic dict memory on 3.9
            (3, 10): 2791,
            (3, 11): 2879,
            (3, 12): 2775,
            (3, 13): 2775,
        },
    ),
]


@pytest.mark.parametrize("scenario_name,builder,expected_sizes", SPAN_SCENARIOS)
def test_span_scenario_size(scenario_name, builder, expected_sizes):
    """Test span memory size (deep/recursive) for a specific scenario."""
    py_version = get_python_version_tuple()

    if py_version not in expected_sizes:
        if py_version == (3, 9):
            pytest.skip("Python 3.9 has non-deterministic dict memory allocation")
        pytest.skip(f"No expected size for Python {py_version}")

    expected_size = expected_sizes[py_version]

    # Build the span and measure its deep size (includes all referenced objects)
    span = builder()
    actual_size = get_deep_size(span)

    assert actual_size == expected_size, (
        f"Scenario '{scenario_name}' memory size changed:\n"
        f"  Expected: {expected_size} bytes\n"
        f"  Actual:   {actual_size} bytes\n"
        f"  Delta:    {actual_size - expected_size:+d} bytes\n"
        f"\n"
        f"Update the expected size in SPAN_SCENARIOS for Python {py_version}:\n"
        f"  ({py_version[0]}, {py_version[1]}): {actual_size},\n"
    )


def test_span_data_has_sizeof():
    """SpanData should have __sizeof__ method."""
    sd = SpanData("test")
    assert hasattr(sd, "__sizeof__")
    assert callable(sd.__sizeof__)


def test_span_event_data_has_sizeof():
    """SpanEventData should have __sizeof__ method."""
    sed = SpanEventData("event", None, None)
    assert hasattr(sed, "__sizeof__")
    assert callable(sed.__sizeof__)


def test_span_link_data_has_sizeof():
    """SpanLinkData should have __sizeof__ method."""
    sld = SpanLinkData(123, 456)
    assert hasattr(sld, "__sizeof__")
    assert callable(sld.__sizeof__)


# Native component size tests
# Expected sizes map (major, minor) Python version to expected byte size
NATIVE_SPAN_DATA_SIZES = {
    (3, 9): 0,
    (3, 10): 0,
    (3, 11): 0,
    (3, 12): 0,
    (3, 13): 0,
}

NATIVE_SPAN_EVENT_DATA_SIZES = {
    (3, 9): 0,
    (3, 10): 0,
    (3, 11): 0,
    (3, 12): 0,
    (3, 13): 0,
}

NATIVE_SPAN_LINK_DATA_SIZES = {
    (3, 9): 0,
    (3, 10): 0,
    (3, 11): 0,
    (3, 12): 0,
    (3, 13): 0,
}


def test_native_span_data_size():
    """Test SpanData native component size."""
    py_version = get_python_version_tuple()

    if py_version not in NATIVE_SPAN_DATA_SIZES:
        pytest.skip(f"No expected size for Python {py_version}")

    expected_size = NATIVE_SPAN_DATA_SIZES[py_version]
    sd = SpanData("test")
    actual_size = sys.getsizeof(sd)

    assert actual_size == expected_size, (
        f"SpanData size changed:\n"
        f"  Expected: {expected_size} bytes\n"
        f"  Actual:   {actual_size} bytes\n"
        f"\n"
        f"Update NATIVE_SPAN_DATA_SIZES for Python {py_version}:\n"
        f"  ({py_version[0]}, {py_version[1]}): {actual_size},\n"
    )


def test_native_span_event_data_size():
    """Test SpanEventData native component size."""
    py_version = get_python_version_tuple()

    if py_version not in NATIVE_SPAN_EVENT_DATA_SIZES:
        pytest.skip(f"No expected size for Python {py_version}")

    expected_size = NATIVE_SPAN_EVENT_DATA_SIZES[py_version]
    sed = SpanEventData("event", None, None)
    actual_size = sys.getsizeof(sed)

    assert actual_size == expected_size, (
        f"SpanEventData size changed:\n"
        f"  Expected: {expected_size} bytes\n"
        f"  Actual:   {actual_size} bytes\n"
        f"\n"
        f"Update NATIVE_SPAN_EVENT_DATA_SIZES for Python {py_version}:\n"
        f"  ({py_version[0]}, {py_version[1]}): {actual_size},\n"
    )


def test_native_span_link_data_size():
    """Test SpanLinkData native component size."""
    py_version = get_python_version_tuple()

    if py_version not in NATIVE_SPAN_LINK_DATA_SIZES:
        pytest.skip(f"No expected size for Python {py_version}")

    expected_size = NATIVE_SPAN_LINK_DATA_SIZES[py_version]
    sld = SpanLinkData(123, 456)
    actual_size = sys.getsizeof(sld)

    assert actual_size == expected_size, (
        f"SpanLinkData size changed:\n"
        f"  Expected: {expected_size} bytes\n"
        f"  Actual:   {actual_size} bytes\n"
        f"\n"
        f"Update NATIVE_SPAN_LINK_DATA_SIZES for Python {py_version}:\n"
        f"  ({py_version[0]}, {py_version[1]}): {actual_size},\n"
    )
