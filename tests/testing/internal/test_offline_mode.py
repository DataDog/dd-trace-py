"""Unit tests for ddtrace.testing.internal.offline_mode."""

from __future__ import annotations

import pytest

import ddtrace.testing.internal.offline_mode as offline_module
from ddtrace.testing.internal.offline_mode import OfflineMode
from ddtrace.testing.internal.offline_mode import _parse_manifest_version
from ddtrace.testing.internal.offline_mode import _validate_manifest
from ddtrace.testing.internal.offline_mode import get_offline_mode
from ddtrace.testing.internal.offline_mode import resolve_rlocation


@pytest.fixture(autouse=True)
def reset_offline_singleton(monkeypatch):
    """Reset the module-level singleton before each test."""
    monkeypatch.setattr(offline_module, "_offline_mode", None)


# ---------------------------------------------------------------------------
# resolve_rlocation
# ---------------------------------------------------------------------------


class TestResolveRlocation:
    def test_existing_path_returned_as_is(self, tmp_path):
        f = tmp_path / "manifest.txt"
        f.write_text("1")
        assert resolve_rlocation(str(f)) == str(f)

    def test_runfiles_dir_resolution(self, tmp_path, monkeypatch):
        runfiles = tmp_path / "runfiles"
        runfiles.mkdir()
        target = runfiles / "repo" / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("data")

        monkeypatch.setenv("RUNFILES_DIR", str(runfiles))
        result = resolve_rlocation("repo/file.txt")
        assert result == str(target)

    def test_runfiles_manifest_file_resolution(self, tmp_path, monkeypatch):
        actual = tmp_path / "actual_file.txt"
        actual.write_text("data")

        manifest = tmp_path / "MANIFEST"
        manifest.write_text(f"repo/file.txt {actual}\n")

        # Ensure RUNFILES_DIR won't match (doesn't contain the file).
        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(manifest))

        result = resolve_rlocation("repo/file.txt")
        assert result == str(actual)

    def test_runfiles_manifest_file_no_match_returns_original(self, tmp_path, monkeypatch):
        manifest = tmp_path / "MANIFEST"
        manifest.write_text("other/path /somewhere\n")

        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.setenv("RUNFILES_MANIFEST_FILE", str(manifest))

        result = resolve_rlocation("repo/missing.txt")
        assert result == "repo/missing.txt"

    def test_runfiles_manifest_file_unreadable_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.setenv("RUNFILES_MANIFEST_FILE", "/nonexistent/MANIFEST")

        result = resolve_rlocation("some/path.txt")
        assert result == "some/path.txt"

    def test_test_srcdir_resolution(self, tmp_path, monkeypatch):
        target = tmp_path / "repo" / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("data")

        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.delenv("RUNFILES_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("TEST_SRCDIR", str(tmp_path))

        result = resolve_rlocation("repo/file.txt")
        assert result == str(target)

    def test_fallback_returns_original_path(self, monkeypatch):
        monkeypatch.delenv("RUNFILES_DIR", raising=False)
        monkeypatch.delenv("RUNFILES_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("TEST_SRCDIR", raising=False)

        result = resolve_rlocation("totally/fake/path.txt")
        assert result == "totally/fake/path.txt"

    def test_resolution_priority_direct_path_first(self, tmp_path, monkeypatch):
        """Direct filesystem path wins over RUNFILES_DIR."""
        f = tmp_path / "real.txt"
        f.write_text("direct")

        runfiles = tmp_path / "runfiles"
        runfiles.mkdir()
        (runfiles / "real.txt").write_text("runfiles")

        monkeypatch.setenv("RUNFILES_DIR", str(runfiles))
        result = resolve_rlocation(str(f))
        assert result == str(f)


# ---------------------------------------------------------------------------
# _validate_manifest
# ---------------------------------------------------------------------------


class TestValidateManifest:
    def test_valid_version_1(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("1")
        assert _validate_manifest(str(m)) is True

    def test_valid_version_1_with_whitespace(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("  1  \n")
        assert _validate_manifest(str(m)) is True

    def test_unsupported_version(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("2")
        assert _validate_manifest(str(m)) is False

    def test_version_zero(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("0")
        assert _validate_manifest(str(m)) is False

    def test_non_integer_content(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("not_a_number")
        assert _validate_manifest(str(m)) is False

    def test_missing_file(self, tmp_path):
        assert _validate_manifest(str(tmp_path / "nonexistent.txt")) is False

    def test_empty_file(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("")
        assert _validate_manifest(str(m)) is False

    def test_version_assignment_syntax(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("version=1\n")
        assert _validate_manifest(str(m)) is True

    def test_version_assignment_with_spaces(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("version = 1\n")
        assert _validate_manifest(str(m)) is True

    def test_leading_blank_lines_skipped(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("\n  \n1\n2\n")
        assert _validate_manifest(str(m)) is True

    def test_invalid_assignment_value(self, tmp_path):
        m = tmp_path / "manifest.txt"
        m.write_text("version = nope\n")
        assert _validate_manifest(str(m)) is False

    def test_non_version_assignment_preserved(self, tmp_path):
        """An assignment where the key is not 'version' should be treated as raw line."""
        m = tmp_path / "manifest.txt"
        m.write_text("manifest = 1\n")
        assert _validate_manifest(str(m)) is False  # "manifest = 1" is not a valid integer


class TestParseManifestVersion:
    def test_plain_number(self):
        assert _parse_manifest_version("1") == "1"

    def test_version_assignment(self):
        assert _parse_manifest_version("version=1") == "1"

    def test_version_assignment_with_spaces(self):
        assert _parse_manifest_version("version = 1") == "1"

    def test_non_version_assignment_preserved(self):
        assert _parse_manifest_version("manifest = 1") == "manifest = 1"

    def test_empty_string(self):
        assert _parse_manifest_version("") == ""


# ---------------------------------------------------------------------------
# OfflineMode
# ---------------------------------------------------------------------------


class TestOfflineMode:
    def test_both_modes_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        mode = OfflineMode()

        assert mode.manifest_enabled is False
        assert mode.payload_files_enabled is False
        assert mode.test_optimization_dir is None
        assert mode.output_dir is None

    def test_manifest_mode_enabled_valid_manifest(self, tmp_path, monkeypatch):
        manifest = tmp_path / ".testoptimization" / "manifest.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("1")

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(manifest))
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        mode = OfflineMode()

        assert mode.manifest_enabled is True
        assert mode.test_optimization_dir == str(manifest.parent)
        assert mode.payload_files_enabled is False

    def test_manifest_mode_disabled_invalid_version(self, tmp_path, monkeypatch):
        manifest = tmp_path / "manifest.txt"
        manifest.write_text("99")

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(manifest))

        mode = OfflineMode()

        assert mode.manifest_enabled is False
        assert mode.test_optimization_dir is None

    def test_manifest_mode_disabled_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(tmp_path / "nonexistent" / "manifest.txt"))

        mode = OfflineMode()

        assert mode.manifest_enabled is False

    def test_payload_files_mode_enabled(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "undeclared_outputs"
        output_dir.mkdir()

        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))

        mode = OfflineMode()

        assert mode.payload_files_enabled is True
        assert mode.output_dir == str(output_dir)
        assert mode.manifest_enabled is False

    def test_payload_files_mode_disabled_when_env_false(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "false")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))

        mode = OfflineMode()

        assert mode.payload_files_enabled is False

    def test_payload_files_mode_disabled_without_output_dir(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.delenv("TEST_UNDECLARED_OUTPUTS_DIR", raising=False)

        mode = OfflineMode()

        assert mode.payload_files_enabled is False
        assert mode.output_dir is None

    def test_both_modes_enabled_simultaneously(self, tmp_path, monkeypatch):
        manifest = tmp_path / ".testoptimization" / "manifest.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("1")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(manifest))
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))

        mode = OfflineMode()

        assert mode.manifest_enabled is True
        assert mode.payload_files_enabled is True


class TestOfflineModeHelpers:
    def test_cache_file_path_when_manifest_enabled(self, tmp_path, monkeypatch):
        manifest = tmp_path / ".testoptimization" / "manifest.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("1")
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", str(manifest))

        mode = OfflineMode()
        result = mode.cache_file_path("cache/http/settings.json")
        assert result == str(tmp_path / ".testoptimization" / "cache" / "http" / "settings.json")

    def test_cache_file_path_when_manifest_disabled(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)

        mode = OfflineMode()
        assert mode.cache_file_path("cache/http/settings.json") is None

    def test_payload_output_dir_when_enabled(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", "true")
        monkeypatch.setenv("TEST_UNDECLARED_OUTPUTS_DIR", str(output_dir))

        mode = OfflineMode()
        assert mode.payload_output_dir("tests") == str(output_dir / "payloads" / "tests")
        assert mode.payload_output_dir("coverage") == str(output_dir / "payloads" / "coverage")

    def test_payload_output_dir_when_disabled(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        mode = OfflineMode()
        assert mode.payload_output_dir("tests") is None


# ---------------------------------------------------------------------------
# get_offline_mode singleton
# ---------------------------------------------------------------------------


class TestGetOfflineMode:
    def test_returns_offline_mode_instance(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        mode = get_offline_mode()
        assert isinstance(mode, OfflineMode)

    def test_singleton_same_object_on_repeated_calls(self, monkeypatch):
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        first = get_offline_mode()
        second = get_offline_mode()
        assert first is second

    def test_singleton_reset_by_fixture(self, monkeypatch):
        """After the autouse fixture resets the singleton, a new OfflineMode is created."""
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_MANIFEST_FILE", raising=False)
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES", raising=False)

        # The singleton was already reset by autouse fixture.
        assert offline_module._offline_mode is None
        mode = get_offline_mode()
        assert offline_module._offline_mode is mode
