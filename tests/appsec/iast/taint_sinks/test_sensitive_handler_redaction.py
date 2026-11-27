"""Unit tests for the default IAST redaction value pattern."""

import time
import tracemalloc

import pytest

from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import SensitiveHandler
from ddtrace.appsec._iast._evidence_redaction.default_sensitive_analyzer import default_sensitive_analyzer
from ddtrace.appsec._iast.constants import MAX_REDACTION_VALUE_LENGTH
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.settings.asm import config as asm_config


_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


class TestJwtValuePattern:
    """The JWT branch of the default value pattern detects tokens in linear time."""

    def test_default_value_pattern_uses_linear_jwt_branch(self):
        pattern = asm_config._iast_redaction_value_pattern
        assert r"ey[I-L][\w=-]+\.ey[I-L]" not in pattern
        assert r"ey[I-L](?:(?!ey[I-L][\w=-])[\w=-])+\.ey[I-L][\w=-]+(\.[\w.+\/=-]+)?" in pattern

    @pytest.mark.parametrize(
        "value",
        [
            _JWT,
            _JWT.rsplit(".", 1)[0],
            _JWT.rsplit(".", 1)[0] + ".",
            "EYJA.EYLB",
            "Bearer " + _JWT,
            "a=1&jwt=" + _JWT + "&b=2",
            '{"id_token":"' + _JWT + '"}',
            "line1\n" + _JWT + "\nline3",
            "x" + _JWT,
            "eyJaeyJb.eyJc",
            "eyJZLeyj.EYLay",
        ],
        ids=[
            "three-segments",
            "two-segments",
            "trailing-dot",
            "upper-case",
            "bearer",
            "query",
            "json",
            "lines",
            "glued",
            "header-with-inner-start",
            "header-ending-in-start",
        ],
    )
    def test_jwt_value_is_sensitive(self, value):
        handler = SensitiveHandler()
        assert handler.is_sensible_value(value)
        ranges = default_sensitive_analyzer(Evidence(value=value), handler._name_pattern, handler._value_pattern)
        assert ranges == [{"start": 0, "end": len(value)}]

    @pytest.mark.parametrize(
        "value",
        ["eyJ" * 1000, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJabc.notjwt", "hello world"],
        ids=["repeated-header-start", "header-only", "second-segment-not-jwt", "plain-text"],
    )
    def test_non_jwt_value_is_not_sensitive(self, value):
        handler = SensitiveHandler()
        assert not handler.is_sensible_value(value)
        assert default_sensitive_analyzer(Evidence(value=value), handler._name_pattern, handler._value_pattern) == []

    # Both sizes stay within MAX_REDACTION_VALUE_LENGTH so the JWT pattern runs. Bounds sit well above the
    # linear pattern (about 1 ms) and below the old quadratic one (about 0.1 s and 0.4 s locally).
    @pytest.mark.parametrize("size,bound", [(16_384, 0.05), (32_768, 0.2)], ids=["16384", "32768"])
    def test_jwt_value_redaction_takes_linear_time(self, size, bound):
        handler = SensitiveHandler()
        value = ("eyJ" * (size // 3 + 1))[:size]
        start = time.perf_counter()
        handler.is_sensible_value(value)
        default_sensitive_analyzer(Evidence(value=value), handler._name_pattern, handler._value_pattern)
        assert time.perf_counter() - start < bound

    def test_value_over_max_length_is_redacted_without_regex(self):
        handler = SensitiveHandler()
        value = "eyJ" + "a" * MAX_REDACTION_VALUE_LENGTH
        tracemalloc.start()
        try:
            assert handler.is_sensible_value(value)
            ranges = default_sensitive_analyzer(Evidence(value=value), handler._name_pattern, handler._value_pattern)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert ranges == [{"start": 0, "end": len(value)}]
        # The value pattern would allocate several MiB on this input; skipping it keeps the peak small.
        assert peak < 1024 * 1024

    def test_value_at_max_length_still_uses_regex(self):
        handler = SensitiveHandler()
        assert not handler.is_sensible_value("a" * MAX_REDACTION_VALUE_LENGTH)
