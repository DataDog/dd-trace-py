"""Unit tests for SensitiveHandler.to_redacted_json focusing on sensitive/tainted range overlap."""

import copy

from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import SensitiveHandler
from ddtrace.appsec._iast.reporter import Source


def _make_source(name="param", value="tainted_value", origin="http.request.parameter"):
    return Source(origin=origin, name=name, value=value)


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


class TestRemoveMethod:
    """Unit tests for SensitiveHandler._remove."""

    def test_no_intersection(self):
        handler = SensitiveHandler()
        result = handler._remove({"start": 0, "end": 5}, {"start": 10, "end": 15})
        assert result == [{"start": 0, "end": 5}]

    def test_full_containment(self):
        handler = SensitiveHandler()
        result = handler._remove({"start": 5, "end": 10}, {"start": 0, "end": 15})
        assert result == []

    def test_partial_overlap_left(self):
        handler = SensitiveHandler()
        result = handler._remove({"start": 5, "end": 15}, {"start": 0, "end": 10})
        assert result == [{"start": 10, "end": 15}]

    def test_partial_overlap_right(self):
        handler = SensitiveHandler()
        result = handler._remove({"start": 0, "end": 10}, {"start": 5, "end": 15})
        assert result == [{"start": 0, "end": 5}]

    def test_remove_middle_produces_two_residuals(self):
        handler = SensitiveHandler()
        result = handler._remove({"start": 0, "end": 20}, {"start": 5, "end": 15})
        assert result == [{"start": 0, "end": 5}, {"start": 15, "end": 20}]
