import os
from unittest.mock import patch

import pytest

from ddtrace.internal._instrumentation_enabled import FALSE_VALUES
from ddtrace.internal._instrumentation_enabled import TRUE_VALUES
from ddtrace.internal._instrumentation_enabled import getenv_bool
from ddtrace.internal._instrumentation_enabled import resolve_instrumentation_enabled


class TestGetenvBool:
    def test_getenv_bool_none_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            result = getenv_bool("NONEXISTENT_VAR")
            assert result is None

    @pytest.mark.parametrize("true_val", TRUE_VALUES)
    def test_getenv_bool_true_values(self, true_val):
        with patch.dict(os.environ, {"TEST_VAR": true_val}):
            result = getenv_bool("TEST_VAR")
            assert result is True

    @pytest.mark.parametrize(
        "val", ["TRUE", "True", "YES", "Yes", "ON", "On", "ENABLE", "Enable", "ENABLED", "Enabled"]
    )
    def test_getenv_bool_true_values_case_insensitive(self, val):
        with patch.dict(os.environ, {"TEST_VAR": val}):
            result = getenv_bool("TEST_VAR")
            assert result is True

    @pytest.mark.parametrize("false_val", FALSE_VALUES)
    def test_getenv_bool_false_values(self, false_val):
        with patch.dict(os.environ, {"TEST_VAR": false_val}):
            result = getenv_bool("TEST_VAR")
            assert result is False

    @pytest.mark.parametrize(
        "val", ["FALSE", "False", "NO", "No", "OFF", "Off", "DISABLE", "Disable", "DISABLED", "Disabled"]
    )
    def test_getenv_bool_false_values_case_insensitive(self, val):
        with patch.dict(os.environ, {"TEST_VAR": val}):
            result = getenv_bool("TEST_VAR")
            assert result is False

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("  true  ", True),
            ("\ttrue\t", True),
            (" false ", False),
            ("\nfalse\n", False),
            ("  1  ", True),
            ("  0  ", False),
        ],
    )
    def test_getenv_bool_whitespace_handling(self, val, expected):
        with patch.dict(os.environ, {"TEST_VAR": val}):
            result = getenv_bool("TEST_VAR")
            assert result == expected

    @pytest.mark.parametrize("invalid_val", ["maybe", "invalid", "2", "-1", ""])
    def test_getenv_bool_invalid_values(self, invalid_val):
        with patch.dict(os.environ, {"TEST_VAR": invalid_val}):
            result = getenv_bool("TEST_VAR")
            assert result is None

    def test_getenv_bool_empty_string(self):
        with patch.dict(os.environ, {"TEST_VAR": ""}):
            result = getenv_bool("TEST_VAR")
            assert result is None


class TestResolveInstrumentationEnabled:
    def test_resolve_instrumentation_enabled_default_true(self):
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_instrumentation_enabled()
            assert result is True

    def test_resolve_instrumentation_enabled_default_false(self):
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_instrumentation_enabled(global_default=False)
            assert result is False

    def test_resolve_instrumentation_enabled_dd_instrumentation_disabled_true(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "true"}):
            result = resolve_instrumentation_enabled()
            assert result is False

    def test_resolve_instrumentation_enabled_dd_instrumentation_disabled_false(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "false"}):
            result = resolve_instrumentation_enabled()
            assert result is True

    def test_resolve_instrumentation_enabled_dd_instrumentation_disabled_1(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "1"}):
            result = resolve_instrumentation_enabled()
            assert result is False

    def test_resolve_instrumentation_enabled_dd_instrumentation_disabled_0(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "0"}):
            result = resolve_instrumentation_enabled()
            assert result is True

    def test_resolve_instrumentation_enabled_civisibility_enabled_true(self):
        with patch.dict(os.environ, {"DD_CIVISIBILITY_ENABLED": "true"}):
            result = resolve_instrumentation_enabled()
            assert result is True

    def test_resolve_instrumentation_enabled_civisibility_enabled_false(self):
        with patch.dict(os.environ, {"DD_CIVISIBILITY_ENABLED": "false"}):
            result = resolve_instrumentation_enabled()
            assert result is False

    def test_resolve_instrumentation_enabled_civisibility_enabled_1(self):
        with patch.dict(os.environ, {"DD_CIVISIBILITY_ENABLED": "1"}):
            result = resolve_instrumentation_enabled()
            assert result is True

    def test_resolve_instrumentation_enabled_civisibility_enabled_0(self):
        with patch.dict(os.environ, {"DD_CIVISIBILITY_ENABLED": "0"}):
            result = resolve_instrumentation_enabled()
            assert result is False

    def test_resolve_instrumentation_enabled_precedence_disabled_wins(self):
        # _DD_INSTRUMENTATION_DISABLED has higher precedence
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "true", "DD_CIVISIBILITY_ENABLED": "true"}):
            result = resolve_instrumentation_enabled()
            assert result is False

    def test_resolve_instrumentation_enabled_precedence_disabled_false_civisibility_false(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "false", "DD_CIVISIBILITY_ENABLED": "false"}):
            result = resolve_instrumentation_enabled()
            assert result is True  # First env var wins

    def test_resolve_instrumentation_enabled_precedence_disabled_invalid_civisibility_true(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "invalid", "DD_CIVISIBILITY_ENABLED": "true"}):
            result = resolve_instrumentation_enabled()
            assert result is True  # Falls through to second env var

    def test_resolve_instrumentation_enabled_both_invalid_uses_default(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "invalid", "DD_CIVISIBILITY_ENABLED": "invalid"}):
            result = resolve_instrumentation_enabled()
            assert result is True  # Uses global default

        result_false_default = resolve_instrumentation_enabled(global_default=False)
        assert result_false_default is False

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("TRUE", False),  # _DD_INSTRUMENTATION_DISABLED=TRUE means disabled
            ("FALSE", True),  # _DD_INSTRUMENTATION_DISABLED=FALSE means enabled
            ("Enable", False),  # _DD_INSTRUMENTATION_DISABLED=Enable means disabled
            ("Disable", True),  # _DD_INSTRUMENTATION_DISABLED=Disable means enabled
        ],
    )
    def test_resolve_instrumentation_enabled_case_insensitive(self, val, expected):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": val}):
            result = resolve_instrumentation_enabled()
            assert result == expected

    def test_resolve_instrumentation_enabled_whitespace_handling(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "  true  "}):
            result = resolve_instrumentation_enabled()
            assert result is False

        with patch.dict(os.environ, {"DD_CIVISIBILITY_ENABLED": "\tfalse\t"}):
            result = resolve_instrumentation_enabled()
            assert result is False


class TestEdgeCases:
    def test_getenv_bool_unicode_values(self):
        with patch.dict(os.environ, {"TEST_VAR": "tru\u00e9"}):  # "trué"
            result = getenv_bool("TEST_VAR")
            assert result is None

    def test_resolve_instrumentation_enabled_only_whitespace_env_var(self):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": "   "}):
            result = resolve_instrumentation_enabled()
            assert result is True  # Empty string after strip should use default

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("2", True),  # Invalid, should fall back to default
            ("-1", True),  # Invalid, should fall back to default
            ("1.0", True),  # Invalid, should fall back to default
            ("00", True),  # Invalid, should fall back to default
        ],
    )
    def test_resolve_instrumentation_enabled_numeric_strings(self, val, expected):
        with patch.dict(os.environ, {"_DD_INSTRUMENTATION_DISABLED": val}):
            result = resolve_instrumentation_enabled()
            assert result == expected
