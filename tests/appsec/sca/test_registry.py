"""Unit tests for InstrumentationRegistry."""

import threading

from ddtrace.appsec.sca._registry import InstrumentationRegistry
from ddtrace.appsec.sca._registry import get_global_registry


def test_registry_add_target():
    """Test adding a target to the registry."""
    registry = InstrumentationRegistry()

    registry.add_target("module.path:function")

    assert registry.has_target("module.path:function")
    assert not registry.is_instrumented("module.path:function")
    assert not registry.is_pending("module.path:function")


def test_registry_add_target_pending():
    """Test adding a pending target."""
    registry = InstrumentationRegistry()

    registry.add_target("module.path:function", pending=True)

    assert registry.has_target("module.path:function")
    assert not registry.is_instrumented("module.path:function")
    assert registry.is_pending("module.path:function")


def test_registry_add_target_duplicate():
    """Test that adding duplicate target is idempotent."""
    registry = InstrumentationRegistry()

    registry.add_target("module.path:function")
    registry.add_target("module.path:function")  # Should not create duplicate

    assert registry.has_target("module.path:function")


def test_registry_remove_target():
    """Test removing a target from the registry."""
    registry = InstrumentationRegistry()

    registry.add_target("module.path:function")
    assert registry.has_target("module.path:function")

    registry.remove_target("module.path:function")
    assert not registry.has_target("module.path:function")


def test_registry_remove_nonexistent():
    """Test that removing non-existent target doesn't error."""
    registry = InstrumentationRegistry()

    # Should not raise
    registry.remove_target("nonexistent")


def test_registry_mark_instrumented():
    """Test marking a target as instrumented."""
    registry = InstrumentationRegistry()

    # Create a dummy code object
    def dummy_func():
        pass

    original_code = dummy_func.__code__

    registry.add_target("module.path:function", pending=True)
    registry.mark_instrumented("module.path:function", original_code)

    assert registry.is_instrumented("module.path:function")
    assert not registry.is_pending("module.path:function")


def test_registry_record_hit():
    """Test recording hits on instrumented functions."""
    registry = InstrumentationRegistry()

    registry.add_target("module.path:function")

    # Record some hits
    registry.record_hit("module.path:function")
    registry.record_hit("module.path:function")
    registry.record_hit("module.path:function")

    assert registry.get_hit_count("module.path:function") == 3


def test_registry_record_hit_nonexistent():
    """Test recording hit on non-existent target doesn't error."""
    registry = InstrumentationRegistry()

    # Should not raise
    registry.record_hit("nonexistent")


def test_registry_get_hit_count_nonexistent():
    """Test getting hit count for non-existent target returns 0."""
    registry = InstrumentationRegistry()

    assert registry.get_hit_count("nonexistent") == 0


def test_registry_get_stats():
    """Test getting stats for all targets."""
    registry = InstrumentationRegistry()

    def dummy_func():
        pass

    registry.add_target("target1", pending=False)
    registry.add_target("target2", pending=True)
    registry.mark_instrumented("target1", dummy_func.__code__)
    registry.record_hit("target1")
    registry.record_hit("target1")

    stats = registry.get_stats()

    assert "target1" in stats
    assert stats["target1"]["is_instrumented"] is True
    assert stats["target1"]["is_pending"] is False
    assert stats["target1"]["hit_count"] == 2

    assert "target2" in stats
    assert stats["target2"]["is_instrumented"] is False
    assert stats["target2"]["is_pending"] is True
    assert stats["target2"]["hit_count"] == 0


def test_registry_clear():
    """Test clearing all targets."""
    registry = InstrumentationRegistry()

    registry.add_target("target1")
    registry.add_target("target2")
    assert registry.has_target("target1")
    assert registry.has_target("target2")

    registry.clear()

    assert not registry.has_target("target1")
    assert not registry.has_target("target2")


def test_registry_thread_safety():
    """Test that registry is thread-safe."""
    registry = InstrumentationRegistry()
    errors = []

    def add_targets(start, end):
        try:
            for i in range(start, end):
                registry.add_target(f"target{i}")
        except Exception as e:
            errors.append(e)

    def record_hits():
        try:
            for i in range(100):
                registry.record_hit(f"target{i % 10}")
        except Exception as e:
            errors.append(e)

    # Add targets from multiple threads
    threads = [
        threading.Thread(target=add_targets, args=(0, 50)),
        threading.Thread(target=add_targets, args=(50, 100)),
        threading.Thread(target=record_hits),
        threading.Thread(target=record_hits),
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    # Should complete without errors
    assert len(errors) == 0

    # Verify targets were added
    for i in range(100):
        assert registry.has_target(f"target{i}")


def test_get_global_registry():
    """Test getting global registry singleton."""
    registry1 = get_global_registry()
    registry2 = get_global_registry()

    # Should return same instance
    assert registry1 is registry2


def test_get_global_registry_thread_safety():
    """Test that global registry creation is thread-safe."""
    registries = []
    errors = []

    def get_registry():
        try:
            registries.append(get_global_registry())
        except Exception as e:
            errors.append(e)

    # Get registry from multiple threads
    threads = [threading.Thread(target=get_registry) for _ in range(10)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    # Should complete without errors
    assert len(errors) == 0

    # All should be same instance
    assert len(set(id(r) for r in registries)) == 1
