"""Tests for the DD_CIVISIBILITY_CODE_COVERAGE_ENABLED tri-state override.

The variable is tri-state: when unset, the backend-provided settings (plus the private
_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE force-enable) decide whether code coverage
collection is enabled; when set, it forces code coverage collection on/off for the run.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility import CIVisibility


class TestCodeCoverageEnabledOverride:
    @pytest.mark.parametrize(
        "env_var_value, coverage_enabled_by_api, force_enable_coverage, expected",
        [
            # Unset: the backend setting (and the private force-enable variable) decide.
            (None, False, False, False),
            (None, False, True, True),
            (None, True, False, True),
            (None, True, True, True),
            # Set: the env var forces code coverage collection on/off, overriding everything else.
            ("true", False, False, True),
            ("true", True, False, True),
            ("false", False, False, False),
            ("false", False, True, False),
            ("false", True, False, False),
            ("false", True, True, False),
        ],
    )
    def test_should_collect_coverage(
        self, monkeypatch, env_var_value, coverage_enabled_by_api, force_enable_coverage, expected
    ):
        if env_var_value is not None:
            monkeypatch.setenv("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED", env_var_value)
        if force_enable_coverage:
            monkeypatch.setenv("_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE", "true")

        assert CIVisibility._should_collect_coverage(coverage_enabled_by_api) is expected

    @pytest.mark.parametrize(
        "env_var_value, coverage_enabled_by_api, expected",
        [
            (None, False, False),
            (None, True, True),
            ("true", False, True),
            ("true", True, True),
            ("false", False, False),
            ("false", True, False),
        ],
    )
    def test_should_collect_coverage_classmethod(self, monkeypatch, env_var_value, coverage_enabled_by_api, expected):
        if env_var_value is not None:
            monkeypatch.setenv("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED", env_var_value)

        fake_instance = SimpleNamespace(
            _api_settings=SimpleNamespace(coverage_enabled=coverage_enabled_by_api),
        )
        with mock.patch.object(CIVisibility, "_instance", fake_instance):
            assert CIVisibility.should_collect_coverage() is expected

    def test_should_collect_coverage_classmethod_without_instance(self, monkeypatch):
        monkeypatch.setenv("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED", "true")

        with mock.patch.object(CIVisibility, "_instance", None):
            assert CIVisibility.should_collect_coverage() is False
