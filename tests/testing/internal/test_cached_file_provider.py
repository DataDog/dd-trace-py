"""Unit tests for ddtrace.testing.internal.cached_file_provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from ddtrace.testing.internal.cached_file_provider import CachedFileDataProvider
from ddtrace.testing.internal.cached_file_provider import _read_cache_json
from ddtrace.testing.internal.constants import ITRSkippingLevel
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


def make_provider(tmp_path: Path, itr_level: ITRSkippingLevel = ITRSkippingLevel.TEST) -> CachedFileDataProvider:
    telemetry = Mock()
    return CachedFileDataProvider(
        test_optimization_dir=str(tmp_path),
        itr_skipping_level=itr_level,
        telemetry_api=telemetry,
    )


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# _read_cache_json
# ---------------------------------------------------------------------------


class TestReadCacheJson:
    def test_valid_json_file(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        result = _read_cache_json(str(f))
        assert result == {"key": "value"}

    def test_file_not_found_returns_none(self, tmp_path):
        result = _read_cache_json(str(tmp_path / "missing.json"))
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        result = _read_cache_json(str(f))
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        result = _read_cache_json(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# CachedFileDataProvider.get_settings
# ---------------------------------------------------------------------------

_SETTINGS_RESPONSE = {
    "data": {
        "attributes": {
            "code_coverage": True,
            "tests_skipping": True,
            "itr_enabled": True,
            "require_git": False,
            "known_tests_enabled": True,
            "flaky_test_retries_enabled": False,
            "coverage_report_upload_enabled": False,
            "early_flake_detection": {
                "enabled": False,
                "slow_test_retries": {"5s": 10, "10s": 5, "30s": 3, "5m": 2},
                "faulty_session_threshold": 30,
            },
            "test_management": {
                "enabled": False,
                "attempt_to_fix_retries": 20,
            },
        }
    }
}


class TestGetSettings:
    def test_returns_parsed_settings(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "settings.json", _SETTINGS_RESPONSE)
        provider = make_provider(tmp_path)
        settings = provider.get_settings()
        assert settings.coverage_enabled is True
        assert settings.skipping_enabled is True
        assert settings.itr_enabled is True

    def test_missing_file_returns_default_settings(self, tmp_path):
        provider = make_provider(tmp_path)
        settings = provider.get_settings()
        assert settings.itr_enabled is False
        assert settings.skipping_enabled is False
        assert settings.coverage_enabled is False

    def test_malformed_json_structure_returns_default_settings(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "settings.json", {"wrong": "structure"})
        provider = make_provider(tmp_path)
        settings = provider.get_settings()
        assert settings.itr_enabled is False

    def test_telemetry_is_called_on_success(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "settings.json", _SETTINGS_RESPONSE)
        telemetry = Mock()
        provider = CachedFileDataProvider(
            test_optimization_dir=str(tmp_path),
            itr_skipping_level=ITRSkippingLevel.TEST,
            telemetry_api=telemetry,
        )
        provider.get_settings()
        telemetry.record_settings.assert_called_once()


# ---------------------------------------------------------------------------
# CachedFileDataProvider.get_known_tests
# ---------------------------------------------------------------------------

_KNOWN_TESTS_RESPONSE = {
    "data": {
        "attributes": {
            "tests": {
                "mymodule": {
                    "test_suite.py": ["test_alpha", "test_beta"],
                    "other_suite.py": ["test_gamma"],
                }
            }
        }
    }
}


class TestGetKnownTests:
    def test_returns_test_refs(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "known_tests.json", _KNOWN_TESTS_RESPONSE)
        provider = make_provider(tmp_path)
        known = provider.get_known_tests()

        module_ref = ModuleRef("mymodule")
        suite_a = SuiteRef(module_ref, "test_suite.py")
        suite_b = SuiteRef(module_ref, "other_suite.py")

        assert TestRef(suite_a, "test_alpha") in known
        assert TestRef(suite_a, "test_beta") in known
        assert TestRef(suite_b, "test_gamma") in known
        assert len(known) == 3

    def test_missing_file_returns_empty_set(self, tmp_path):
        provider = make_provider(tmp_path)
        assert provider.get_known_tests() == set()

    def test_malformed_structure_returns_empty_set(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "known_tests.json", {"unexpected": "data"})
        provider = make_provider(tmp_path)
        assert provider.get_known_tests() == set()

    def test_telemetry_count_recorded(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "known_tests.json", _KNOWN_TESTS_RESPONSE)
        telemetry = Mock()
        provider = CachedFileDataProvider(
            test_optimization_dir=str(tmp_path),
            itr_skipping_level=ITRSkippingLevel.TEST,
            telemetry_api=telemetry,
        )
        provider.get_known_tests()
        telemetry.record_known_tests_count.assert_called_once_with(3)


# ---------------------------------------------------------------------------
# CachedFileDataProvider.get_test_management_properties
# ---------------------------------------------------------------------------

_TEST_MGMT_RESPONSE = {
    "data": {
        "attributes": {
            "modules": {
                "mymodule": {
                    "suites": {
                        "test_suite.py": {
                            "tests": {
                                "test_quarantined": {
                                    "properties": {"quarantined": True, "disabled": False, "attempt_to_fix": False}
                                },
                                "test_disabled": {
                                    "properties": {"quarantined": False, "disabled": True, "attempt_to_fix": False}
                                },
                                "test_normal": {"properties": {}},
                            }
                        }
                    }
                }
            }
        }
    }
}


class TestGetTestManagementProperties:
    def test_returns_correct_properties(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "test_management.json", _TEST_MGMT_RESPONSE)
        provider = make_provider(tmp_path)
        props = provider.get_test_management_properties()

        module_ref = ModuleRef("mymodule")
        suite_ref = SuiteRef(module_ref, "test_suite.py")

        quarantined_ref = TestRef(suite_ref, "test_quarantined")
        disabled_ref = TestRef(suite_ref, "test_disabled")
        normal_ref = TestRef(suite_ref, "test_normal")

        assert props[quarantined_ref].quarantined is True
        assert props[quarantined_ref].disabled is False
        assert props[disabled_ref].disabled is True
        assert props[normal_ref].quarantined is False
        assert props[normal_ref].disabled is False

    def test_missing_file_returns_empty_dict(self, tmp_path):
        provider = make_provider(tmp_path)
        assert provider.get_test_management_properties() == {}

    def test_malformed_structure_returns_empty_dict(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "test_management.json", {"bad": "structure"})
        provider = make_provider(tmp_path)
        assert provider.get_test_management_properties() == {}


# ---------------------------------------------------------------------------
# CachedFileDataProvider.get_skippable_tests
# ---------------------------------------------------------------------------

_SKIPPABLE_RESPONSE = {
    "data": [
        {
            "type": "test",
            "attributes": {
                "name": "test_skip_me",
                "suite": "test_suite.py",
                "configurations": {"test.bundle": "mymodule"},
            },
        },
        {
            "type": "test",
            "attributes": {
                "name": "test_also_skip",
                "suite": "test_suite.py",
                "configurations": {"test.bundle": "mymodule"},
            },
        },
        {
            "type": "unknown_type",
            "attributes": {
                "name": "ignored",
                "suite": "test_suite.py",
                "configurations": {"test.bundle": "mymodule"},
            },
        },
    ],
    "meta": {"correlation_id": "abc-123"},
}


class TestGetSkippableTests:
    """Skippable tests are a hard no-op in manifest mode (matches Go behavior).

    Cached skippable decisions should not be applied in hermetic Bazel runs,
    regardless of whether a cache file exists.
    """

    def test_always_returns_empty_even_with_cache_file(self, tmp_path):
        write_json(tmp_path / "cache" / "http" / "skippable_tests.json", _SKIPPABLE_RESPONSE)
        provider = make_provider(tmp_path, itr_level=ITRSkippingLevel.TEST)
        skippable, correlation_id = provider.get_skippable_tests()
        assert skippable == set()
        assert correlation_id is None

    def test_returns_empty_without_cache_file(self, tmp_path):
        provider = make_provider(tmp_path)
        skippable, correlation_id = provider.get_skippable_tests()
        assert skippable == set()
        assert correlation_id is None


# ---------------------------------------------------------------------------
# No-op methods
# ---------------------------------------------------------------------------


class TestNoOpMethods:
    def test_get_known_commits_returns_empty_list(self, tmp_path):
        provider = make_provider(tmp_path)
        assert provider.get_known_commits(["abc", "def"]) == []

    def test_send_git_pack_file_returns_none(self, tmp_path):
        provider = make_provider(tmp_path)
        result = provider.send_git_pack_file(Path("/fake/pack.pack"))
        assert result is None

    def test_upload_coverage_report_returns_false(self, tmp_path):
        provider = make_provider(tmp_path)
        result = provider.upload_coverage_report(b"data", "lcov", {})
        assert result is False

    def test_close_is_a_noop(self, tmp_path):
        provider = make_provider(tmp_path)
        provider.close()  # Should not raise
