"""Tests for ddtrace.internal.telemetry.dependency module."""

import json

import pytest

from ddtrace.internal.telemetry.dependency import DependencyEntry
from ddtrace.internal.telemetry.dependency import ReachabilityMetadata
from ddtrace.internal.telemetry.dependency import attach_reachability_metadata
from ddtrace.internal.telemetry.dependency import register_cve_metadata


@pytest.fixture(autouse=True)
def _restore_sca_config():
    """Save and restore tracer_config._sca_enabled to prevent cross-test contamination."""
    from ddtrace.internal.settings._config import config as tracer_config

    saved = tracer_config._sca_enabled
    yield
    tracer_config._sca_enabled = saved


class TestReachabilityMetadata:
    def test_to_telemetry_dict_serializes_value_as_json_string(self):
        meta = ReachabilityMetadata(
            type="reachability",
            value={"id": "CVE-2024-1234", "reached": [{"path": "mod.sub", "symbol": "func", "line": 10}]},
        )
        result = meta.to_telemetry_dict()
        assert result["type"] == "reachability"
        assert isinstance(result["value"], str)
        parsed = json.loads(result["value"])
        assert parsed["id"] == "CVE-2024-1234"
        assert isinstance(parsed["reached"], list)
        assert len(parsed["reached"]) == 1
        assert parsed["reached"][0]["path"] == "mod.sub"
        assert parsed["reached"][0]["symbol"] == "func"
        assert parsed["reached"][0]["line"] == 10

    def test_sent_tracking(self):
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": []})
        assert meta.is_sent is False
        meta._mark_sent()
        assert meta.is_sent is True

    def test_cve_id_property(self):
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": []})
        assert meta.cve_id == "CVE-1"

    def test_cve_id_property_missing(self):
        meta = ReachabilityMetadata(type="reachability", value={"reached": []})
        assert meta.cve_id is None

    def test_add_reached_entry_first_hit(self):
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": []})
        assert meta.add_reached_entry("mod.views", "func1", 33) is True
        assert len(meta.value["reached"]) == 1
        assert meta.value["reached"][0] == {"path": "mod.views", "symbol": "func1", "line": 33}
        assert meta.is_sent is False

    def test_add_reached_entry_max_one(self):
        """RFC says reporting a single occurrence is sufficient — max 1."""
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": []})
        assert meta.add_reached_entry("mod.views", "func1", 33) is True
        assert meta.add_reached_entry("mod.services", "func2", 44) is False
        assert len(meta.value["reached"]) == 1

    def test_add_reached_entry_marks_unsent(self):
        meta = ReachabilityMetadata(type="reachability", value={"id": "CVE-1", "reached": []})
        meta._mark_sent()
        assert meta.is_sent is True
        meta.add_reached_entry("mod", "func", 1)
        assert meta.is_sent is False


class TestDependencyEntry:
    def test_to_telemetry_dict_metadata_none(self):
        """metadata=None -> no metadata key (SCA not active)."""
        entry = DependencyEntry(name="requests", version="2.28.0")
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0"}
        assert "metadata" not in result

    def test_to_telemetry_dict_metadata_empty_list(self):
        """metadata=[] -> metadata key with empty list (SCA active, no findings)."""
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0", "metadata": []}

    def test_to_telemetry_dict_with_metadata(self):
        """metadata with entries -> metadata key with serialized entries."""
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.add_metadata("CVE-2024-1234", "requests.sessions", "send", 1)
        result = entry.to_telemetry_dict()
        assert result["name"] == "requests"
        assert result["version"] == "2.28.0"
        assert len(result["metadata"]) == 1
        assert result["metadata"][0]["type"] == "reachability"
        parsed = json.loads(result["metadata"][0]["value"])
        assert parsed["id"] == "CVE-2024-1234"
        assert isinstance(parsed["reached"], list)
        assert len(parsed["reached"]) == 1

    def test_add_metadata_same_cve_first_hit_wins(self):
        """Same CVE from different call sites — first hit wins (max reached=1)."""
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.add_metadata("CVE-1", "mymodule.views", "func1", 33) is True
        assert entry.add_metadata("CVE-1", "views.endpoint2", "func2", 456) is False
        assert len(entry.metadata) == 1
        assert len(entry.metadata[0].value["reached"]) == 1

    def test_add_metadata_different_cves(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.add_metadata("CVE-1", "mod", "func", 1) is True
        assert entry.add_metadata("CVE-2", "mod", "func", 1) is True
        assert len(entry.metadata) == 2

    def test_add_metadata_rejects_no_cve_id(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.add_metadata("") is False
        assert entry.metadata is None

    def test_add_metadata_initializes_list_from_none(self):
        """add_metadata on a None-metadata entry initializes the list."""
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.metadata is None
        entry.add_metadata("CVE-1")
        assert entry.metadata is not None
        assert len(entry.metadata) == 1

    def test_add_metadata_register_cve_empty_reached(self):
        """Registering a CVE without call-site creates reached=[]."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        assert entry.add_metadata("CVE-1") is True
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value == {"id": "CVE-1", "reached": []}

    def test_add_metadata_register_then_hit(self):
        """Register CVE with reached=[], then hit fills it."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        entry.add_metadata("CVE-1")  # register with reached=[]
        assert entry.metadata[0].value["reached"] == []
        assert entry.add_metadata("CVE-1", "mod.views", "func1", 33) is True
        assert len(entry.metadata) == 1
        assert len(entry.metadata[0].value["reached"]) == 1
        assert entry.metadata[0].value["reached"][0] == {"path": "mod.views", "symbol": "func1", "line": 33}

    def test_add_metadata_duplicate_registration_is_idempotent(self):
        """Registering the same CVE twice does not create duplicates."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        assert entry.add_metadata("CVE-1") is True
        assert entry.add_metadata("CVE-1") is False
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
        entry.add_metadata("CVE-1")
        assert entry.needs_report() is True

    def test_needs_report_after_all_metadata_sent(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.mark_initial_sent()
        entry.add_metadata("CVE-1")
        entry.mark_all_metadata_sent()
        assert entry.needs_report() is False

    def test_add_metadata_respects_max_cve_cap(self):
        """add_metadata silently drops CVEs once _MAX_METADATA_ENTRIES is reached."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        for i in range(DependencyEntry._MAX_METADATA_ENTRIES):
            assert entry.add_metadata(f"CVE-{i}") is True
        assert len(entry.metadata) == DependencyEntry._MAX_METADATA_ENTRIES
        assert entry.add_metadata("CVE-overflow") is False
        assert len(entry.metadata) == DependencyEntry._MAX_METADATA_ENTRIES

    def test_get_unsent_metadata(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.add_metadata("CVE-1")
        entry.mark_all_metadata_sent()  # mark CVE-1 as sent
        entry.add_metadata("CVE-2")  # CVE-2 is unsent
        unsent = entry.get_unsent_metadata()
        assert len(unsent) == 1
        assert unsent[0].value["id"] == "CVE-2"

    def test_to_telemetry_dict_only_unsent(self):
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        entry.add_metadata("CVE-1")
        entry.mark_all_metadata_sent()  # CVE-1 is now sent
        entry.add_metadata("CVE-2")  # CVE-2 is unsent
        result = entry.to_telemetry_dict(include_all_metadata=False)
        assert len(result["metadata"]) == 1
        parsed = json.loads(result["metadata"][0]["value"])
        assert parsed["id"] == "CVE-2"

    def test_to_telemetry_dict_include_all(self):
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        entry.add_metadata("CVE-1")
        entry.mark_all_metadata_sent()  # CVE-1 is now sent
        entry.add_metadata("CVE-2")  # CVE-2 is unsent
        result = entry.to_telemetry_dict(include_all_metadata=True)
        assert len(result["metadata"]) == 2

    def test_to_telemetry_dict_all_sent_empty_metadata(self):
        """When all metadata is sent and include_all_metadata=False, empty list."""
        entry = DependencyEntry(name="pkg", version="1.0", metadata=[])
        entry.add_metadata("CVE-1")
        entry.mark_all_metadata_sent()
        result = entry.to_telemetry_dict(include_all_metadata=False)
        assert result["metadata"] == []


class TestAttachReachabilityMetadata:
    def test_attach_to_existing_dependency(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        result = attach_reachability_metadata(deps, "requests", "CVE-2024-1234", "requests.sessions", "send", 10)
        assert result is True
        assert len(deps["requests"].metadata) == 1
        assert deps["requests"].metadata[0].value["id"] == "CVE-2024-1234"
        assert len(deps["requests"].metadata[0].value["reached"]) == 1

    def test_attach_to_unknown_package(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        result = attach_reachability_metadata(deps, "flask", "CVE-2024-9999", "flask.app", "run", 1)
        assert result is False

    def test_attach_multiple_cves_to_same_package(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        attach_reachability_metadata(deps, "requests", "CVE-1", "mod", "func1", 1)
        attach_reachability_metadata(deps, "requests", "CVE-2", "mod", "func2", 5)
        assert len(deps["requests"].metadata) == 2

    def test_attach_same_cve_first_hit_wins(self):
        """Same CVE from different call sites — first hit wins (max reached=1)."""
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        assert attach_reachability_metadata(deps, "requests", "CVE-1", "mod.views", "func1", 33) is True
        assert attach_reachability_metadata(deps, "requests", "CVE-1", "views.ep2", "func2", 456) is False
        assert len(deps["requests"].metadata) == 1
        assert len(deps["requests"].metadata[0].value["reached"]) == 1


class TestRegisterCveMetadata:
    def test_register_cve_to_tracked_dependency(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0", metadata=[])}
        result = register_cve_metadata(deps, "requests", "CVE-1")
        assert result is True
        assert len(deps["requests"].metadata) == 1
        assert deps["requests"].metadata[0].value == {"id": "CVE-1", "reached": []}

    def test_register_cve_to_unknown_package(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0")}
        result = register_cve_metadata(deps, "flask", "CVE-1")
        assert result is False

    def test_register_then_hit(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0", metadata=[])}
        register_cve_metadata(deps, "requests", "CVE-1")
        attach_reachability_metadata(deps, "requests", "CVE-1", "mod", "func", 10)
        assert len(deps["requests"].metadata) == 1
        assert len(deps["requests"].metadata[0].value["reached"]) == 1

    def test_register_duplicate_is_idempotent(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0", metadata=[])}
        assert register_cve_metadata(deps, "requests", "CVE-1") is True
        assert register_cve_metadata(deps, "requests", "CVE-1") is False
        assert len(deps["requests"].metadata) == 1

    def test_register_multiple_cves(self):
        deps = {"requests": DependencyEntry(name="requests", version="2.28.0", metadata=[])}
        register_cve_metadata(deps, "requests", "CVE-1")
        register_cve_metadata(deps, "requests", "CVE-2")
        assert len(deps["requests"].metadata) == 2
        cve_ids = {m.value["id"] for m in deps["requests"].metadata}
        assert cve_ids == {"CVE-1", "CVE-2"}


def _make_writer_and_tracker(sca_enabled=False, deps=None, enabled=True):
    """Shared factory for writer-level tests.

    Sets tracer_config._sca_enabled so DependencyTracker reads the live
    config flag.  Callers should use the _restore_sca_config fixture
    (autouse in this module) to ensure cleanup.
    """
    from unittest.mock import MagicMock

    from ddtrace.internal.settings._config import config as tracer_config
    from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
    from ddtrace.internal.telemetry.writer import TelemetryWriter

    tracer_config._sca_enabled = sca_enabled
    writer = TelemetryWriter.__new__(TelemetryWriter)
    writer._service_lock = MagicMock()
    writer._enabled = enabled
    tracker = DependencyTracker()
    if deps:
        tracker._imported_dependencies = deps
    writer._dependency_tracker = tracker
    return writer, tracker


class TestWriterAttachDependencyMetadata:
    """Writer-level tests for attach_dependency_metadata (via DependencyTracker)."""

    def test_attach_metadata_to_tracked_dependency(self):
        writer, tracker = _make_writer_and_tracker(
            deps={"requests": DependencyEntry(name="requests", version="2.28.0")}
        )

        result = writer.attach_dependency_metadata("requests", "CVE-1", "mod", "func", 1)

        assert result is True
        entry = tracker._imported_dependencies["requests"]
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-1"
        assert len(entry.metadata[0].value["reached"]) == 1

    def test_attach_returns_false_for_unknown_package(self):
        writer, tracker = _make_writer_and_tracker(
            deps={"requests": DependencyEntry(name="requests", version="2.28.0")}
        )

        result = writer.attach_dependency_metadata("flask", "CVE-1", "mod", "func", 1)

        assert result is False
        assert "flask" not in tracker._imported_dependencies

    def test_attach_auto_creates_entry_when_sca_active(self):
        """When SCA is active and package not tracked, auto-create the entry."""
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        with patch("importlib.metadata.version", return_value="2.28.0"):
            result = writer.attach_dependency_metadata("requests", "CVE-1", "mod", "func", 1)

        assert result is True
        assert "requests" in tracker._imported_dependencies
        entry = tracker._imported_dependencies["requests"]
        assert entry.version == "2.28.0"
        assert entry.metadata is not None
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-1"

    def test_attach_does_not_auto_create_when_sca_inactive(self):
        """When SCA is not active, unknown packages return False."""
        writer, tracker = _make_writer_and_tracker(sca_enabled=False)

        result = writer.attach_dependency_metadata("requests", "CVE-1", "mod", "func", 1)

        assert result is False
        assert "requests" not in tracker._imported_dependencies


class TestWriterEnableScaMetadata:
    """Tests for enable_sca_metadata method (via DependencyTracker)."""

    def test_enable_sca_metadata_sets_none_to_empty_list(self):
        writer, tracker = _make_writer_and_tracker(
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
        entry.add_metadata("CVE-1")

        writer, tracker = _make_writer_and_tracker(deps={"requests": entry})

        writer.enable_sca_metadata()

        # Should not overwrite existing metadata
        assert len(tracker._imported_dependencies["requests"].metadata) == 1


class TestWriterReReporting:
    """Tests for _report_dependencies re-reporting behavior with metadata."""

    def test_report_dependencies_rereports_on_new_metadata(self):
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        # Pre-populate with an already-reported dep (SCA active: metadata=[])
        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["requests"] = entry

        # Attach metadata after initial report
        writer.attach_dependency_metadata("requests", "CVE-1", "requests.sessions", "send", 10)

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
        assert isinstance(parsed["reached"], list)
        assert len(parsed["reached"]) == 1

        # Metadata should be marked as sent now
        assert entry.has_unsent_metadata() is False

    def test_report_dependencies_no_rereport_without_new_metadata(self):
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker()

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

        # No new deps, no new metadata -> None
        assert result is None

    def test_rereport_includes_all_metadata_per_rfc(self):
        """Re-report includes ALL metadata (sent + unsent) per RFC."""
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        tracker._imported_dependencies["requests"] = entry

        # Attach first CVE and mark as sent
        writer.attach_dependency_metadata("requests", "CVE-1", "mod.views", "func1", 33)
        entry.mark_all_metadata_sent()

        # Register second CVE (unsent, with reached=[])
        writer.register_cve_metadata("requests", "CVE-2")

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

    def test_rereport_on_cve_registration(self):
        """Registering CVEs after initial dep report triggers re-report with reached=[]."""
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        entry = DependencyEntry(name="requests", version="2.28.0", metadata=[])
        entry.mark_initial_sent()
        entry.mark_all_metadata_sent()
        tracker._imported_dependencies["requests"] = entry

        # Register CVE (simulates CVE load at startup)
        writer.register_cve_metadata("requests", "CVE-1")

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
        assert parsed["reached"] == []

    def test_new_deps_without_sca_have_no_metadata_key(self):
        """New deps created without SCA enabled have no metadata key."""
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker()

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


class TestWriterRegisterCveMetadata:
    """Writer-level tests for register_cve_metadata (via DependencyTracker)."""

    def test_register_cve_auto_creates_entry_when_sca_active(self):
        """register_cve_metadata auto-creates an entry when SCA is active and package untracked."""
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        with patch("importlib.metadata.version", return_value="1.0"):
            result = writer.register_cve_metadata("flask", "CVE-NEW")

        assert result is True
        assert "flask" in tracker._imported_dependencies
        entry = tracker._imported_dependencies["flask"]
        assert entry.version == "1.0"
        assert entry.metadata is not None
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value == {"id": "CVE-NEW", "reached": []}

    def test_register_cve_does_not_auto_create_when_sca_inactive(self):
        writer, tracker = _make_writer_and_tracker(sca_enabled=False)
        result = writer.register_cve_metadata("flask", "CVE-NEW")
        assert result is False
        assert "flask" not in tracker._imported_dependencies

    def test_auto_create_with_version_lookup_failure(self):
        """When importlib.metadata.version raises PackageNotFoundError, entry is created with version=""."""
        from importlib.metadata import PackageNotFoundError
        from unittest.mock import patch

        writer, tracker = _make_writer_and_tracker(sca_enabled=True)

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("unknown-pkg"),
        ):
            result = writer.register_cve_metadata("unknown-pkg", "CVE-1")

        assert result is True
        assert tracker._imported_dependencies["unknown-pkg"].version == ""


class TestNormalizeDepName:
    """Tests for PEP 503 package name canonicalization."""

    def test_lowercase(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("PyYAML") == "pyyaml"

    def test_hyphen_passthrough(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("my-package") == "my-package"

    def test_underscore_to_hyphen(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("my_package") == "my-package"

    def test_dot_to_hyphen(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("my.package") == "my-package"

    def test_mixed_separators_collapsed(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("my_.package") == "my-package"

    def test_already_canonical(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("requests") == "requests"

    def test_complex_name(self):
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        assert _normalize_dep_name("Jinja2") == "jinja2"


class TestTrackerNameNormalization:
    """Tests that DependencyTracker normalizes package names to avoid duplicates and lookup misses."""

    def test_ensure_entry_no_duplicate_with_different_casing(self):
        """_ensure_entry should not create a duplicate when the same package has different casing."""
        from unittest.mock import patch

        from ddtrace.internal.settings._config import config as tracer_config
        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker

        tracer_config._sca_enabled = True
        tracker = DependencyTracker()

        with patch("importlib.metadata.version", return_value="6.0"):
            # Telemetry discovers "PyYAML" first
            tracker._imported_dependencies["pyyaml"] = DependencyEntry(name="PyYAML", version="6.0", metadata=[])
            # SCA tries to ensure "pyyaml" — should find the existing entry
            with tracker._lock:
                tracker._ensure_entry("pyyaml")

        assert len(tracker._imported_dependencies) == 1

    def test_attach_metadata_matches_differently_cased_key(self):
        """attach_metadata with lowercase name should find an entry stored via telemetry discovery."""
        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        tracker = DependencyTracker()
        # Simulate telemetry discovering "PyYAML" (stored under normalized key)
        key = _normalize_dep_name("PyYAML")
        tracker._imported_dependencies[key] = DependencyEntry(name="PyYAML", version="6.0", metadata=[])

        # SCA attaches metadata using lowercase name from CVE data
        result = tracker.attach_metadata("pyyaml", "CVE-2024-1234", "yaml.loader", "load", 10)

        assert result is True
        entry = tracker._imported_dependencies[key]
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value["id"] == "CVE-2024-1234"

    def test_register_cve_matches_differently_cased_key(self):
        """register_cve with lowercase name should find an entry stored via telemetry discovery."""
        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        tracker = DependencyTracker()
        key = _normalize_dep_name("PyYAML")
        tracker._imported_dependencies[key] = DependencyEntry(name="PyYAML", version="6.0", metadata=[])

        result = tracker.register_cve("pyyaml", "CVE-2024-5678")

        assert result is True
        entry = tracker._imported_dependencies[key]
        assert len(entry.metadata) == 1
        assert entry.metadata[0].value == {"id": "CVE-2024-5678", "reached": []}

    def test_update_imported_dependencies_normalizes_keys(self):
        """update_imported_dependencies should store entries under normalized keys."""
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name
        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies

        already_imported = {}
        with patch(
            "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
            return_value=("PyYAML", "6.0"),
        ):
            result = update_imported_dependencies(already_imported, ["yaml"])

        assert len(result) == 1
        assert result[0]["name"] == "PyYAML"  # wire format preserves original name
        normalized = _normalize_dep_name("PyYAML")
        assert normalized in already_imported
        assert already_imported[normalized].name == "PyYAML"

    def test_update_imported_dependencies_deduplicates_with_normalized_keys(self):
        """A second module mapping to the same dist (different casing) should not create a duplicate."""
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name
        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies

        already_imported = {}
        key = _normalize_dep_name("PyYAML")
        already_imported[key] = DependencyEntry(name="PyYAML", version="6.0")

        with patch(
            "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
            return_value=("pyyaml", "6.0"),
        ):
            result = update_imported_dependencies(already_imported, ["yaml.dumper"])

        assert result == []
        assert len(already_imported) == 1

    def test_update_imported_dependencies_swallows_per_module_exception(self):
        """One bad module must not abort the whole batch or propagate to sys.excepthook."""
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies

        def fake_lookup(module_name):
            if module_name == "broken":
                raise RuntimeError("importlib.metadata partially torn down")
            return ("requests", "2.28.0")

        already_imported: dict = {}
        with patch(
            "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
            side_effect=fake_lookup,
        ):
            result = update_imported_dependencies(already_imported, ["broken", "requests"])

        assert len(result) == 1
        assert result[0]["name"] == "requests"
        assert "requests" in already_imported

    def test_update_imported_dependencies_swallows_tracer_config_failure(self):
        """If reading tracer_config fails (e.g. during shutdown), return [] without raising."""
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies

        class BrokenConfig:
            @property
            def _sca_enabled(self):
                raise RuntimeError("config module torn down")

        with patch("ddtrace.internal.settings._config.config", BrokenConfig()):
            result = update_imported_dependencies({}, ["requests"])

        assert result == []

    def test_update_imported_dependencies_swallows_tracer_config_import_failure(self):
        """If the tracer_config import itself fails (e.g. sys.modules torn down), return [] without raising."""
        import sys
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import update_imported_dependencies

        broken_module = type(sys)("ddtrace.internal.settings._config")

        with patch.dict(sys.modules, {"ddtrace.internal.settings._config": broken_module}):
            result = update_imported_dependencies({}, ["requests"])

        assert result == []

    def test_collect_report_marks_sent_with_normalized_lookup(self):
        """collect_report should find entries by normalized key when marking as sent."""
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        tracker = DependencyTracker()

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch("ddtrace.internal.telemetry.dependency_tracker.config") as mock_config,
            patch(
                "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
                return_value=("PyYAML", "6.0"),
            ),
        ):
            mock_config.DEPENDENCY_COLLECTION = True
            mock_modules.get_newly_imported_modules.return_value = {"yaml"}

            result = tracker.collect_report()

        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "PyYAML"

        # Entry should be stored under normalized key and marked as sent
        key = _normalize_dep_name("PyYAML")
        assert key in tracker._imported_dependencies
        assert tracker._imported_dependencies[key]._initial_report_sent is True
