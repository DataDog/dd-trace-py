"""Tests for generic telemetry dependency collection."""

from ddtrace.internal.settings._telemetry import config as telemetry_config
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.internal.telemetry.dependency import DependencyEntry


class TestDependencyEntry:
    def test_to_telemetry_dict_metadata_none(self):
        entry = DependencyEntry(name="requests", version="2.28.0")
        result = entry.to_telemetry_dict()
        assert result == {"name": "requests", "version": "2.28.0"}
        assert "metadata" not in result

    def test_needs_report_new_entry(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        assert entry.needs_report() is True

    def test_needs_report_after_initial_send_no_metadata(self):
        entry = DependencyEntry(name="pkg", version="1.0")
        entry.mark_initial_sent()
        assert entry.needs_report() is False


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
    """Tests that DependencyTracker normalizes package names for dependency collection."""

    def test_update_imported_dependencies_normalizes_keys(self):
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
        assert result[0]["name"] == "PyYAML"
        normalized = _normalize_dep_name("PyYAML")
        assert normalized in already_imported
        assert already_imported[normalized].name == "PyYAML"

    def test_update_imported_dependencies_deduplicates_with_normalized_keys(self):
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

    def test_collect_report_marks_sent_with_normalized_lookup(self):
        from unittest.mock import patch

        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker
        from ddtrace.internal.telemetry.dependency_tracker import _normalize_dep_name

        tracker = DependencyTracker()

        with (
            patch("ddtrace.internal.telemetry.dependency_tracker.modules") as mock_modules,
            patch.object(telemetry_config, "DEPENDENCY_COLLECTION", True),
            patch.object(appsec_telemetry_config, "SCA_ENABLED", False),
            patch(
                "ddtrace.internal.telemetry.dependency_tracker.get_module_distribution_versions",
                return_value=("PyYAML", "6.0"),
            ),
        ):
            mock_modules.get_newly_imported_modules.return_value = {"yaml"}

            result = tracker.collect_report()

        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "PyYAML"

        key = _normalize_dep_name("PyYAML")
        assert key in tracker._imported_dependencies
        assert tracker._imported_dependencies[key]._initial_report_sent is True


class TestSnapshotForHeartbeat:
    """Tests for generic dependency snapshots in extended heartbeats."""

    def test_empty_tracker_returns_empty_list(self):
        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker

        tracker = DependencyTracker()
        assert tracker.snapshot_for_heartbeat() == []

    def test_returns_all_entries_serialized(self):
        from ddtrace.internal.telemetry.dependency_tracker import DependencyTracker

        tracker = DependencyTracker()
        tracker._imported_dependencies = {
            "requests": DependencyEntry(name="requests", version="2.28.0"),
            "flask": DependencyEntry(name="flask", version="3.0.0"),
        }

        snapshot = tracker.snapshot_for_heartbeat()

        assert snapshot == [
            {"name": "requests", "version": "2.28.0"},
            {"name": "flask", "version": "3.0.0"},
        ]
