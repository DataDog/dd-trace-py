"""Unit tests for InstrumentationRegistry."""

import threading

from ddtrace.appsec.sca._registry import InstrumentationRegistry
from ddtrace.appsec.sca._registry import get_global_registry


def test_registry_add_target():
    registry = InstrumentationRegistry()
    registry.add_target("module.path:function")
    assert registry.has_target("module.path:function")
    assert not registry.is_instrumented("module.path:function")


def test_registry_add_target_with_vulnerability_info():
    registry = InstrumentationRegistry()
    registry.add_target(
        "module.path:function",
        package_name="requests",
        cve_ids=["CVE-2024-1234", "CVE-2024-5678"],
        line=42,
    )
    info = registry.get_target_info("module.path:function")
    assert info is not None
    assert info.package_name == "requests"
    assert info.cve_ids == ("CVE-2024-1234", "CVE-2024-5678")
    assert info.line == 42


def test_registry_add_target_pending():
    registry = InstrumentationRegistry()
    registry.add_target("module.path:function", pending=True)
    assert registry.has_target("module.path:function")
    assert not registry.is_instrumented("module.path:function")


def test_registry_add_target_duplicate():
    registry = InstrumentationRegistry()
    registry.add_target("module.path:function")
    registry.add_target("module.path:function")
    assert registry.has_target("module.path:function")


def test_registry_remove_target():
    registry = InstrumentationRegistry()
    registry.add_target("module.path:function")
    assert registry.has_target("module.path:function")
    registry.remove_target("module.path:function")
    assert not registry.has_target("module.path:function")


def test_registry_remove_nonexistent():
    registry = InstrumentationRegistry()
    registry.remove_target("nonexistent")  # Should not raise


def test_registry_mark_instrumented():
    registry = InstrumentationRegistry()

    def dummy_func():
        pass

    original_code = dummy_func.__code__
    registry.add_target("module.path:function", pending=True)
    registry.mark_instrumented("module.path:function", original_code)
    assert registry.is_instrumented("module.path:function")


def test_registry_record_hit():
    registry = InstrumentationRegistry()
    registry.add_target("module.path:function")
    registry.record_hit("module.path:function")
    registry.record_hit("module.path:function")
    registry.record_hit("module.path:function")
    # hit_count is diagnostic only; verify it doesn't raise
    state = registry._targets.get("module.path:function")
    assert state is not None and state.hit_count == 3


def test_registry_record_hit_nonexistent():
    registry = InstrumentationRegistry()
    registry.record_hit("nonexistent")  # Should not raise


def test_registry_get_target_info_nonexistent():
    registry = InstrumentationRegistry()
    assert registry.get_target_info("nonexistent") is None


def test_registry_clear():
    registry = InstrumentationRegistry()
    registry.add_target("target1")
    registry.add_target("target2")
    registry.clear()
    assert not registry.has_target("target1")
    assert not registry.has_target("target2")


def test_registry_thread_safety():
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

    assert len(errors) == 0
    for i in range(100):
        assert registry.has_target(f"target{i}")


def test_registry_merge_cve_ids():
    registry = InstrumentationRegistry()
    registry.add_target(
        "module.path:function",
        package_name="requests",
        cve_ids=["CVE-2024-1234"],
    )
    # Merge new CVEs
    assert registry.merge_cve_ids("module.path:function", ["CVE-2024-5678", "CVE-2024-9999"])
    info = registry.get_target_info("module.path:function")
    assert info is not None
    assert set(info.cve_ids) == {"CVE-2024-1234", "CVE-2024-5678", "CVE-2024-9999"}


def test_registry_merge_cve_ids_no_duplicates():
    registry = InstrumentationRegistry()
    registry.add_target(
        "module.path:function",
        package_name="requests",
        cve_ids=["CVE-2024-1234"],
    )
    # Merging existing CVEs should return False (nothing added)
    assert not registry.merge_cve_ids("module.path:function", ["CVE-2024-1234"])
    info = registry.get_target_info("module.path:function")
    assert info.cve_ids == ("CVE-2024-1234",)


def test_registry_merge_cve_ids_nonexistent():
    registry = InstrumentationRegistry()
    assert not registry.merge_cve_ids("nonexistent", ["CVE-2024-1234"])


def test_get_global_registry():
    registry1 = get_global_registry()
    registry2 = get_global_registry()
    assert registry1 is registry2
