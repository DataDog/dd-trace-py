"""Tests for ddtrace.internal.telemetry.dependency module."""

import json

from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency import ReachabilityMetadata
from ddtrace.internal.telemetry.dependency import attach_reachability_metadata


class TestReachabilityMetadata:
    def test_to_telemetry_dict_serializes_value_as_json_string(self):
        meta = ReachabilityMetadata(
            type="reachability",
            value={"id": "CVE-2024-1234", "reached": True, "path": "mod.sub", "method": "func", "line": 10},
        )
        result = meta.to_telemetry_dict()
        assert result["type"] == "reachability"
        assert isinstance(result["value"], str)
        parsed = json.loads(result["value"])
        assert parsed["id"] == "CVE-2024-1234"
        assert parsed["reached"] is True
        assert parsed["path"] == "mod.sub"
        assert parsed["method"] == "func"
        assert parsed["line"] == 10

    def test_sent_tracking(self):
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1"})
        assert meta.is_sent is False
        meta._mark_sent()
        assert meta.is_sent is True


class TestDependencyEntry:
    def test_to_telemetry_dict_without_metadata(self):
        """Without metadata, wire format is identical to the old dict[str, str] approach."""
        entry = DependencyEntry(name="requests", version="2.28.0")
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0"}
        assert "metadata" not in result

    def test_to_telemetry_dict_with_metadata(self):
        entry = DependencyEntry(name="requests", version="2.28.0")
        entry.add_metadata(
            ReachabilityMetadata(
                type="reachability",
                value={
                    "id": "CVE-2024-1234",
                    "reached": True,
                    "path": "requests.sessions",
                    "method": "send",
                    "line": 1,
                },
            )
        )
        result = entry.to_telemetry_dict()
        assert result["name"] == "requests"
        assert result["version"] == "2.28.0"
        assert len(result["metadata"]) == 1
        assert result["metadata"][0]["type"] == "reachability"
        parsed = json.loads(result["metadata"][0]["value"])
        assert parsed["id"] == "CVE-2024-1234"

    def test_add_metadata_deduplicates_by_cve_id(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        meta1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": True})
        meta2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": True})
        meta3 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2", "reached": True})
        assert entry.add_metadata(meta1) is True
        assert entry.add_metadata(meta2) is False  # duplicate
        assert entry.add_metadata(meta3) is True
        assert len(entry.metadata) == 2

    def test_add_metadata_rejects_no_cve_id(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        meta = ReachabilityMetadata(type="reachability", value={"reached": True})
        assert entry.add_metadata(meta) is False
        assert entry.metadata is None

    def test_needs_report_new_entry(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.needs_report() is True

    def test_needs_report_after_initial_send_no_metadata(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.mark_initial_sent()
        assert entry.needs_report() is False

    def test_needs_report_after_initial_send_with_unsent_metadata(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.mark_initial_sent()
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))
        assert entry.needs_report() is True

    def test_needs_report_after_all_metadata_sent(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.mark_initial_sent()
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))
        entry.mark_all_metadata_sent()
        assert entry.needs_report() is False

    def test_get_unsent_metadata(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        m1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1"})
        m2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2"})
        entry.add_metadata(m1)
        entry.add_metadata(m2)
        m1._mark_sent()
        unsent = entry.get_unsent_metadata()
        assert len(unsent) == 1
        assert unsent[0].value["id"] == "CVE-2"

    def test_to_telemetry_dict_only_unsent(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        m1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1"})
        m2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2"})
        entry.add_metadata(m1)
        entry.add_metadata(m2)
        m1._mark_sent()
        result = entry.to_telemetry_dict(include_all_metadata=False)
        assert len(result["metadata"]) == 1
        parsed = json.loads(result["metadata"][0]["value"])
        assert parsed["id"] == "CVE-2"

    def test_to_telemetry_dict_include_all(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        m1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1"})
        m2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2"})
        entry.add_metadata(m1)
        entry.add_metadata(m2)
        m1._mark_sent()
        result = entry.to_telemetry_dict(include_all_metadata=True)
        assert len(result["metadata"]) == 2

    def test_to_telemetry_dict_no_metadata_key_when_all_sent(self):
        """When all metadata is sent and include_all_metadata=False, no metadata key."""
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))
        entry.mark_all_metadata_sent()
        result = entry.to_telemetry_dict(include_all_metadata=False)
        assert "metadata" not in result


class TestAttachReachabilityMetadata:
    def test_attach_to_existing_dependency(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        result = attach_reachability_metadata(deps, "requests", "CVE-2024-1234", True, "requests.sessions", "send", 10)
        assert result is True
        assert len(deps["requests"].metadata) == 1
        assert deps["requests"].metadata[0].value["id"] == "CVE-2024-1234"

    def test_attach_to_unknown_package(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        result = attach_reachability_metadata(deps, "flask", "CVE-2024-9999", True, "flask.app", "run", 1)
        assert result is False

    def test_attach_multiple_cves_to_same_package(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod", "func1", 1)
        attach_reachability_metadata(deps, "requests", "CVE-2", True, "mod", "func2", 5)
        assert len(deps["requests"].metadata) == 2

    def test_attach_deduplicates_same_cve(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod", "func1", 1) is True
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod", "func1", 1) is False
        assert len(deps["requests"].metadata) == 1


class TestWriterAttachDependencyMetadata:
    """Writer-level tests for attach_dependency_metadata (with locking)."""

    def test_attach_metadata_to_tracked_dependency(self):
        from unittest.mock import MagicMock

        from ddtrace.internal.telemetry.writer import TelemetryWriter

        writer = TelemetryWriter.__new__(TelemetryWriter)
        writer._service_lock = MagicMock()
        writer._imported_dependencies = {
            "requests": DependencyEntry(name="requests", version="2.28.0"),
        }

        result = writer.attach_dependency_metadata("requests", "CVE-1", True, "mod", "func", 1)

        assert result is True
        entry = writer._imported_dependencies["requests"]
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-1"

    def test_attach_returns_false_for_unknown_package(self):
        from unittest.mock import MagicMock

        from ddtrace.internal.telemetry.writer import TelemetryWriter

        writer = TelemetryWriter.__new__(TelemetryWriter)
        writer._service_lock = MagicMock()
        writer._imported_dependencies = {
            "requests": DependencyEntry(name="requests", version="2.28.0"),
        }

        result = writer.attach_dependency_metadata("flask", "CVE-1", True, "mod", "func", 1)

        assert result is False
        assert "flask" not in writer._imported_dependencies
