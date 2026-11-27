"""Unit tests for SensitiveHandler: sensitive/tainted range overlap and the default value pattern."""

import copy
import time
import tracemalloc

import pytest

from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import SensitiveHandler
from ddtrace.appsec._iast._evidence_redaction.default_sensitive_analyzer import default_sensitive_analyzer
from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import MAX_REDACTION_VALUE_LENGTH
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.internal.settings.asm import config as asm_config


def _make_source(name="param", value="tainted_value", origin="http.request.parameter"):
    return Source(origin=origin, name=name, value=value)


def test_default_sensitive_analyzer_without_value():
    handler = SensitiveHandler()
    assert default_sensitive_analyzer(Evidence(), handler._name_pattern, handler._value_pattern) == []


class TestSensitiveFullyInsideTainted:
    """Sensitive range is fully inside a tainted range — the credential must be redacted."""

    def test_sensitive_inside_tainted_redacts_credential(self):
        handler = SensitiveHandler()
        evidence_value = "postgresql://user:password123@host:5432/db"
        source = _make_source(name="connstr", value=evidence_value)

        tainted_ranges = [{"start": 0, "end": len(evidence_value), "source": source}]
        sensitive = [{"start": 17, "end": 28}]  # "password123"
        sources = [source]

        result = handler.to_redacted_json(
            evidence_value,
            copy.deepcopy(sensitive),
            copy.deepcopy(tainted_ranges),
            sources,
        )

        parts = result["redacted_value_parts"]
        full_text = ""
        has_redacted = False
        for part in parts:
            if part.get("redacted"):
                has_redacted = True
            if "value" in part:
                full_text += part["value"]

        assert has_redacted, "Expected at least one redacted value part"
        assert "password123" not in full_text, "Credential leaked unredacted in value parts"

    def test_multiple_sensitives_inside_single_tainted(self):
        handler = SensitiveHandler()
        evidence_value = "user=admin&password=secret123&token=abc"
        source = _make_source(name="query", value=evidence_value)

        tainted_ranges = [{"start": 0, "end": len(evidence_value), "source": source}]
        sensitive = [
            {"start": 5, "end": 10},  # "admin"
            {"start": 20, "end": 29},  # "secret123"
            {"start": 36, "end": 39},  # "abc"
        ]
        sources = [source]

        result = handler.to_redacted_json(
            evidence_value,
            copy.deepcopy(sensitive),
            copy.deepcopy(tainted_ranges),
            sources,
        )

        parts = result["redacted_value_parts"]
        full_text = ""
        for part in parts:
            if "value" in part:
                full_text += part["value"]

        assert "admin" not in full_text
        assert "secret123" not in full_text
        assert 0 in result["redacted_sources"]


class TestSensitiveSpanningTaintedBoundary:
    """Sensitive range extends beyond a tainted range — residual must still be redacted."""

    def test_sensitive_spans_past_tainted_end(self):
        handler = SensitiveHandler()
        evidence_value = "SELECT * FROM t WHERE password='secret' AND x=1"
        source = _make_source(name="input", value="secret")

        tainted_start = 31
        tainted_end = 37  # "secret"
        sensitive_start = 22
        sensitive_end = 38  # "password='secret'"

        tainted_ranges = [{"start": tainted_start, "end": tainted_end, "source": source}]
        sensitive = [{"start": sensitive_start, "end": sensitive_end}]
        sources = [source]

        result = handler.to_redacted_json(
            evidence_value,
            copy.deepcopy(sensitive),
            copy.deepcopy(tainted_ranges),
            sources,
        )

        parts = result["redacted_value_parts"]
        full_text = ""
        for part in parts:
            if "value" in part:
                full_text += part["value"]

        assert "password" not in full_text, "Sensitive prefix 'password' leaked unredacted"

    def test_sensitive_contains_tainted_range(self):
        """Sensitive range fully contains a tainted range — both pre-tainted and post-tainted
        parts of the sensitive range must be redacted.
        """
        handler = SensitiveHandler()
        evidence_value = "BEGIN password='secret123' END rest"
        source = _make_source(name="input", value="secret123")

        tainted_start = 16
        tainted_end = 25  # "secret123"
        sensitive_start = 6
        sensitive_end = 26  # "password='secret123'"

        tainted_ranges = [{"start": tainted_start, "end": tainted_end, "source": source}]
        sensitive = [{"start": sensitive_start, "end": sensitive_end}]
        sources = [source]

        result = handler.to_redacted_json(
            evidence_value,
            copy.deepcopy(sensitive),
            copy.deepcopy(tainted_ranges),
            sources,
        )

        parts = result["redacted_value_parts"]
        full_text = ""
        redacted_count = 0
        for part in parts:
            if part.get("redacted"):
                redacted_count += 1
            if "value" in part:
                full_text += part["value"]

        assert "password" not in full_text, "Sensitive prefix 'password=' leaked unredacted in value parts"
        assert "secret123" not in full_text, "Credential leaked unredacted"
        assert redacted_count >= 2, (
            f"Expected at least 2 redacted parts (pre-tainted + tainted sensitive), got {redacted_count}"
        )

    def test_sensitive_across_two_tainted_ranges(self):
        """Sensitive range starts before the first tainted range and extends into the second."""
        handler = SensitiveHandler()
        evidence_value = "key=password123&token=abc"
        source1 = _make_source(name="p1", value="password")
        source2 = _make_source(name="p2", value="123&token")

        tainted_ranges = [
            {"start": 4, "end": 12, "source": source1},  # "password"
            {"start": 12, "end": 21, "source": source2},  # "123&token"
        ]
        sensitive = [{"start": 4, "end": 15}]  # "password123"
        sources = [source1, source2]

        result = handler.to_redacted_json(
            evidence_value,
            copy.deepcopy(sensitive),
            copy.deepcopy(tainted_ranges),
            sources,
        )

        parts = result["redacted_value_parts"]
        full_text = ""
        for part in parts:
            if "value" in part:
                full_text += part["value"]

        assert "password" not in full_text, "Sensitive content leaked across tainted boundary"


class TestRangeRedactionEdgeCases:
    """Overlap, bounds, and multi-source edge cases in SensitiveHandler.to_redacted_json."""

    @pytest.mark.parametrize(
        "evidence,sensitive,tainted,source_value,expected_parts",
        [
            # Sensitive range starts before a transformed tainted range.
            (
                "X-Custom: Bearer secret",
                [{"start": 10, "end": 23}],
                [(11, 13)],
                "hello",
                [
                    {"value": "X-Custom: "},
                    {"redacted": True},
                    {"redacted": True, "source": 0, "pattern": "**"},
                    {"redacted": True},
                ],
            ),
            # Partial left overlap with transformed taint extending past sensitive end.
            (
                "Bearer secret",
                [{"start": 0, "end": 7}],
                [(5, 10)],
                "hello",
                [
                    {"redacted": True},
                    {"redacted": True, "source": 0, "pattern": "**"},
                    {"source": 0, "value": "sec"},
                    {"value": "ret"},
                ],
            ),
            # Transformed taint whose sensitive slice appears past len(part_value) in source.value.
            (
                "xy!",
                [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
                [(0, 3)],
                "xay---xy",
                [{"redacted": True, "source": 0, "pattern": "gh"}, {"source": 0, "value": "!"}],
            ),
            # Empty sensitive marker inside tainted delimiters.
            (
                "SELECT ''",
                [{"start": 8, "end": 8}],
                [(7, 9)],
                "''",
                [
                    {"value": "SELECT "},
                    {"value": "'", "source": 0},
                    {"redacted": True},
                    {"value": "'", "source": 0},
                ],
            ),
            # Out-of-order, overlapping, inverted, and negative-start sensitive ranges.
            (
                "abcd",
                [{"start": 1, "end": 4}, {"start": -1, "end": 1}, {"start": 3, "end": 2}],
                [],
                "",
                [{"redacted": True}],
            ),
        ],
    )
    def test_overlap_and_normalization_cases(self, evidence, sensitive, tainted, source_value, expected_parts):
        handler = SensitiveHandler()
        src = _make_source(name="q", value=source_value)
        sensitive_copy = copy.deepcopy(sensitive)
        tainted_ranges = [{"start": s, "end": e, "source": src} for s, e in tainted]
        tainted_copy = list(tainted_ranges)
        result = handler.to_redacted_json(evidence, sensitive, tainted_ranges, [src] if tainted else [])

        assert sensitive == sensitive_copy
        assert tainted_ranges == tainted_copy
        assert result["redacted_value_parts"] == expected_parts

    @pytest.mark.parametrize(
        "s0_kwargs,s1_kwargs",
        [
            (
                {"name": "q", "value": "hello", "origin": OriginType.HEADER},
                {"name": "user", "value": "world", "origin": OriginType.QUERY},
            ),
            (
                {"name": "p", "value": "first", "origin": OriginType.PARAMETER},
                {"name": "p", "value": "xy", "origin": OriginType.PARAMETER},
            ),
        ],
    )
    def test_multi_source_overlap_redacts_matching_source(self, s0_kwargs, s1_kwargs):
        handler = SensitiveHandler()
        s0, s1 = _make_source(**s0_kwargs), _make_source(**s1_kwargs)
        result = handler.to_redacted_json(
            "-- zxy!",
            [{"start": 3, "end": 6}],
            [{"start": 4, "end": 6, "source": s1}],
            [s0, s1],
        )

        assert result["redacted_sources"] == [1]
        assert not s0.redacted
        assert s1.redacted
        plain = "".join(p["value"] for p in result["redacted_value_parts"] if "value" in p)
        assert "xy" not in plain

    @pytest.mark.parametrize(
        "sensitive,tainted", [([{"start": 0, "end": 3}], (0, 3)), ([{"start": 0, "end": 5}], (2, 4))]
    )
    def test_unresolved_source_fails_closed(self, sensitive, tainted):
        handler = SensitiveHandler()
        missing = _make_source(name="missing", value="abc")
        result = handler.to_redacted_json(
            "abcde",
            sensitive,
            [{"start": tainted[0], "end": tainted[1], "source": missing}],
            [],
        )

        assert all(p.get("source") != -1 for p in result["redacted_value_parts"])
        assert "abc" not in "".join(p["value"] for p in result["redacted_value_parts"] if "value" in p)

    @pytest.mark.parametrize(
        "source_name,second_prefix,second_suffix",
        [
            ("param", "SELECT '", "'"),
            ("password", "SELECT * FROM t ORDER BY ", ""),
        ],
    )
    @pytest.mark.parametrize("reverse_order", [False, True])
    def test_shared_source_redacted_across_multiple_vulnerabilities(
        self, iast_context_defaults, source_name, second_prefix, second_suffix, reverse_order
    ):
        t1 = taint_pyobject(
            pyobject="QX", source_name=source_name, source_value="QXMN", source_origin=OriginType.PARAMETER
        )
        t2 = taint_pyobject(
            pyobject="MN", source_name=source_name, source_value="QXMN", source_origin=OriginType.PARAMETER
        )
        v1 = Vulnerability(
            type=VULN_SQL_INJECTION,
            evidence=Evidence(value=add_aspect("SELECT '", add_aspect(t1, "'"))),
            location=Location(path="a.py", line=1, spanId=1),
        )
        v2 = Vulnerability(
            type=VULN_SQL_INJECTION,
            evidence=Evidence(value=add_aspect(second_prefix, add_aspect(t2, second_suffix))),
            location=Location(path="a.py", line=2, spanId=1),
        )
        vulns = [v2, v1] if reverse_order else [v1, v2]
        report = IastSpanReporter(vulnerabilities=set(vulns))
        for v in vulns:
            report.add_ranges_to_evidence_and_extract_sources(v)
        report.vulnerabilities = vulns  # type: ignore[assignment]
        result = report.build_and_scrub_value_parts()

        assert len(result["sources"]) == 1
        assert result["sources"][0]["redacted"] is True
        assert "value" not in result["sources"][0]
        for vuln in result["vulnerabilities"]:
            plain = "".join(p["value"] for p in vuln["evidence"]["valueParts"] if "value" in p)
            assert "QX" not in plain and "MN" not in plain


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
