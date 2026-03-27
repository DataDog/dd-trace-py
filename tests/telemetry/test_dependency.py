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
    def test_to_telemetry_dict_metadata_none(self):
        """metadata=None → no metadata key (SCA not active)."""
        entry = DependencyEntry(name="requests", version="2.28.0")
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0"}
        assert "metadata" not in result

    def test_to_telemetry_dict_metadata_empty_list(self):
        """metadata=[] → metadata key with empty list (SCA active, no findings)."""
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0", "metadata": []}

    def test_to_telemetry_dict_with_metadata(self):
        """metadata with entries → metadata key with serialized entries."""
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
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

    def test_add_metadata_deduplicates_exact_composite_key(self):
        """Exact duplicate (same CVE + path + method + line) is rejected."""
        entry = DependencyEntry(name="pkg", version="1.0")
        meta1 = ReachabilityMetadata(
            type="reachability", value={"id": "CVE-1", "reached": True, "path": "mod", "method": "func", "line": 10}
        )
        meta2 = ReachabilityMetadata(
            type="reachability", value={"id": "CVE-1", "reached": True, "path": "mod", "method": "func", "line": 10}
        )
        assert entry.add_metadata(meta1) is True
        assert entry.add_metadata(meta2) is False  # exact duplicate
        assert len(entry.metadata) == 1

    def test_add_metadata_same_cve_different_callsite(self):
        """Same CVE from different call sites creates separate entries per RFC."""
        entry = DependencyEntry(name="pkg", version="1.0")
        meta1 = ReachabilityMetadata(
            type="reachability",
            value={"id": "CVE-1", "reached": True, "path": "mymodule.views", "method": "func1", "line": 33},
        )
        meta2 = ReachabilityMetadata(
            type="reachability",
            value={"id": "CVE-1", "reached": True, "path": "views.endpoint2", "method": "func2", "line": 456},
        )
        assert entry.add_metadata(meta1) is True
        assert entry.add_metadata(meta2) is True  # different call site, same CVE
        assert len(entry.metadata) == 2

    def test_add_metadata_different_cves(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        meta1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": True})
        meta2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2", "reached": True})
        assert entry.add_metadata(meta1) is True
        assert entry.add_metadata(meta2) is True
        assert len(entry.metadata) == 2

    def test_add_metadata_rejects_no_cve_id(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        meta = ReachabilityMetadata(type="reachability", value={"reached": True})
        assert entry.add_metadata(meta) is False
        assert entry.metadata is None

    def test_add_metadata_initializes_list_from_none(self):
        """add_metadata on a None-metadata entry initializes the list."""
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.metadata is None
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))
        assert entry.metadata is not None
        assert len(entry.metadata) == 1

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
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
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
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        m1 = ReachabilityMetadata(type="reachability", value={"id": "CVE-1"})
        m2 = ReachabilityMetadata(type="reachability", value={"id": "CVE-2"})
        entry.add_metadata(m1)
        entry.add_metadata(m2)
        m1._mark_sent()
        result = entry.to_telemetry_dict(include_all_metadata=True)
        assert len(result["metadata"]) == 2

    def test_to_telemetry_dict_all_sent_empty_metadata(self):
        """When all metadata is sent and include_all_metadata=False, empty list."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))
        entry.mark_all_metadata_sent()
        result = entry.to_telemetry_dict(include_all_metadata=False)
        assert result["metadata"] == []


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

    def test_attach_deduplicates_exact_composite_key(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod", "func1", 1) is True
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod", "func1", 1) is False
        assert len(deps["requests"].metadata) == 1

    def test_attach_same_cve_different_callsite(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "mod.views", "func1", 33) is True
        assert attach_reachability_metadata(deps, "requests", "CVE-1", True, "views.ep2", "func2", 456) is True
        assert len(deps["requests"].metadata) == 2


class TestWriterAttachDependencyMetadata:
    """Writer-level tests for attach_dependency_metadata (via DependencyTracker)."""

    def _make_writer_with_tracker(self, sca_enabled=False, deps=None):
        from unittest.mock import MagicMock

        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.writer import TelemetryWriter

        writer = TelemetryWriter.__new__(TelemetryWriter)
        writer._service_lock = MagicMock()
        tracker = DependencyTracker()
        tracker._sca_metadata_enabled = sca_enabled
        if deps:
            tracker._imported_dependencies = deps
        writer._dependency_tracker = tracker
        return writer, tracker

    def test_attach_metadata_to_tracked_dependency(self):
        writer, tracker = self._make_writer_with_tracker(
            deps={"requests": DependencyEntry(name="requests", version="2.28.0")}
        )

        result = writer.attach_dependency_metadata("requests", "CVE-1", True, "mod", "func", 1)

        assert result is True
        entry = tracker._imported_dependencies["requests"]
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-1"

    def test_attach_returns_false_for_unknown_package(self):
        writer, tracker = self._make_writer_with_tracker(
            deps={"requests": DependencyEntry(name="requests", version="2.28.0")}
        )

        result = writer.attach_dependency_metadata("flask", "CVE-1", True, "mod", "func", 1)

        assert result is False
        assert "flask" not in tracker._imported_dependencies

    def test_attach_auto_creates_entry_when_sca_active(self):
        """When SCA is active and package not tracked, auto-create the entry."""
        from unittest.mock import patch

        writer, tracker = self._make_writer_with_tracker(sca_enabled=True)

        with patch("ddtrace.internal.telemetry.dependency_tracker.importlib_metadata_version", return_value="2.28.0"):
            result = writer.attach_dependency_metadata("requests", "CVE-1", True, "mod", "func", 1)

        assert result is True
        assert "requests" in tracker._imported_dependencies
        entry = tracker._imported_dependencies["requests"]
        assert entry.version == "2.28.0"
        assert entry.metadata is not None
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-1"

    def test_attach_does_not_auto_create_when_sca_inactive(self):
        """When SCA is not active, unknown packages return False."""
        writer, tracker = self._make_writer_with_tracker(sca_enabled=False)

        result = writer.attach_dependency_metadata("requests", "CVE-1", True, "mod", "func", 1)

        assert result is False
        assert "requests" not in tracker._imported_dependencies


class TestWriterEnableScaMetadata:
    """Tests for enable_sca_metadata method (via DependencyTracker)."""

    def _make_writer_with_tracker(self, deps=None):
        from unittest.mock import MagicMock

        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.writer import TelemetryWriter

        writer = TelemetryWriter.__new__(TelemetryWriter)
        writer._service_lock = MagicMock()
        tracker = DependencyTracker()
        if deps:
            tracker._imported_dependencies = deps
        writer._dependency_tracker = tracker
        return writer, tracker

    def test_enable_sca_metadata_sets_none_to_empty_list(self):
        writer, tracker = self._make_writer_with_tracker(
            deps={
                "requests": DependencyEntry(name="requests", version="2.28.0"),
                "flask": DependencyEntry(name="flask", version="3.0.0"),
            }
        )
        assert tracker._imported_dependencies["requests"].metadata is None
        assert tracker._imported_dependencies["flask"].metadata is None

        writer.enable_sca_metadata()

        assert tracker._imported_dependencies["requests"].metadata == []
        assert tracker._imported_dependencies["flask"].metadata == []

    def test_enable_sca_metadata_preserves_existing_metadata(self):
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.add_metadata(ReachabilityMetadata(type="reachability", value={"id": "CVE-1"}))

        writer, tracker = self._make_writer_with_tracker(deps={"requests": entry})

        writer.enable_sca_metadata()

        # Should not overwrite existing metadata
        assert len(tracker._imported_dependencies["requests"].metadata) == 1


class TestWriterReReporting:
    """Tests for _report_dependencies re-reporting behavior with metadata."""

    def _make_writer(self):
        from unittest.mock import MagicMock

        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.writer import TelemetryWriter

        writer = TelemetryWriter.__new__(TelemetryWriter)
        writer._service_lock = MagicMock()
        tracker = DependencyTracker()
        writer._dependency_tracker = tracker
        writer._enabled = True
        return writer, tracker

    def test_report_dependencies_rereports_on_new_metadata(self):
        from unittest.mock import patch

        writer, tracker = self._make_writer()

        # Pre-populate with an already-reported dep (SCA active: metadata=[])
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["requests"] = entry

        # Attach metadata after initial report
        writer.attach_dependency_metadata("requests", "CVE-1", True, "requests.sessions", "send", 10)

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
        ):
            mock_config.DEPENDENCY_COLLECTION = True
            mock_modules.get_newly_imported_modules.return_value = set()

            result = writer._report_dependencies()

        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "requests"
        assert len(result[0]["metadata"]) == 1
        parsed = json.loads(result[0]["metadata"][0]["value"])
        assert parsed["id"] == "CVE-1"

        # Metadata should be marked as sent now
        assert entry.has_unsent_metadata() is False

    def test_report_dependencies_no_rereport_without_new_metadata(self):
        from unittest.mock import patch

        writer, tracker = self._make_writer()

        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["requests"] = entry

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
        ):
            mock_config.DEPENDENCY_COLLECTION = True
            mock_modules.get_newly_imported_modules.return_value = set()

            result = writer._report_dependencies()

        # No new deps, no new metadata → None
        assert result is None

    def test_rereport_includes_all_metadata_per_rfc(self):
        """Re-report includes ALL metadata (sent + unsent) per RFC."""
        from unittest.mock import patch

        writer, tracker = self._make_writer()

        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["requests"] = entry

        # Attach first CVE and mark as sent
        writer.attach_dependency_metadata("requests", "CVE-1", True, "mod.views", "func1", 33)
        entry.mark_all_metadata_sent()

        # Attach second CVE (unsent)
        writer.attach_dependency_metadata("requests", "CVE-2", True, "views.ep2", "func2", 456)

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
        ):
            mock_config.DEPENDENCY_COLLECTION = True
            mock_modules.get_newly_imported_modules.return_value = set()

            result = writer._report_dependencies()

        assert result is not None
        assert len(result) == 1
        # Should include ALL metadata (both CVE-1 and CVE-2)
        assert len(result[0]["metadata"]) == 2

    def test_new_deps_without_sca_have_no_metadata_key(self):
        """New deps created without SCA enabled have no metadata key."""
        from unittest.mock import patch

        writer, tracker = self._make_writer()

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
            patch(
                "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
                return_value=("requests", "2.28.0"),
            ),
        ):
            mock_config.DEPENDENCY_COLLECTION = True
            mock_modules.get_newly_imported_modules.return_value = {"requests"}

            result = writer._report_dependencies()

        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "requests"
        assert "metadata" not in result[0]
