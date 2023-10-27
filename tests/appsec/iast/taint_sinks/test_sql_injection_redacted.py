import copy

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.internal import core


if python_supported_by_iast():
    from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
    from ddtrace.appsec._iast.taint_sinks.sql_injection import SqlInjection

from ddtrace.internal.utils.cache import LFUCache
from tests.utils import override_env


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_no_match():
    ev = Evidence(value="SomeEvidenceValue")
    orig_ev = ev.value
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert not v.evidence.redacted
        assert v.evidence.value == orig_ev


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_source_name_match():
    ev = Evidence(value="'SomeEvidenceValue'")
    len_ev = len(ev.value) - 2
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="secret", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.redacted
        assert v.evidence.pattern == "'%s'" % ("*" * len_ev)
        assert not v.evidence.value


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_source_value_match():
    ev = Evidence(value="'SomeEvidenceValue'")
    len_ev = len(ev.value) - 2
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="somepassword")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.redacted
        assert v.evidence.pattern == "'%s'" % ("*" * len_ev)
        assert not v.evidence.value


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_evidence_value_match_also_redacts_source_value():
    ev = Evidence(value="'SomeSecretPassword'")
    len_ev = len(ev.value) - 2
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeSecretPassword")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.redacted
        assert v.evidence.pattern == "'%s'" % ("*" * len_ev)
        assert not v.evidence.value
    for s in redacted_report.sources:
        assert s.redacted
        assert s.pattern == "abcdefghijklmnopqr"
        assert not s.value


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_valueparts():
    ev = Evidence(
        valueParts=[
            {"value": "SELECT * FROM users WHERE password = '"},
            {"value": "1234", "source": 0},
            {"value": ":{SHA1}'"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "SELECT * FROM users WHERE password = '"},
            {"source": 0, "pattern": "abcd", "redacted": True},
            {"pattern": "*******'", "redacted": True},
        ]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_valueparts_username_not_tainted():
    ev = Evidence(
        valueParts=[
            {"value": "SELECT * FROM users WHERE username = '"},
            {"value": "pepito"},
            {"value": "' AND password = '"},
            {"value": "secret", "source": 0},
            {"value": "'"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "SELECT * FROM users WHERE username = '"},
            {"pattern": "******", "redacted": True},
            {"value": "' AND password = '"},
            {"pattern": "abcdef", "redacted": True, "source": 0},
            {"value": "'"},
        ]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_redacted_report_valueparts_username_tainted():
    ev = Evidence(
        valueParts=[
            {"value": "SELECT * FROM users WHERE username = '"},
            {"value": "pepito", "source": 0},
            {"value": "' AND password = '"},
            {"value": "secret", "source": 0},
            {"value": "'"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "SELECT * FROM users WHERE username = '"},
            {"pattern": "abcdef", "redacted": True, "source": 0},
            {"value": "' AND password = '"},
            {"pattern": "abcdef", "redacted": True, "source": 0},
            {"value": "'"},
        ]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_regression_ci_failure():
    ev = Evidence(
        valueParts=[
            {"value": "SELECT tbl_name FROM sqlite_"},
            {"value": "master", "source": 0},
            {"value": "WHERE tbl_name LIKE 'password'"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = SqlInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "SELECT tbl_name FROM sqlite_"},
            {"value": "master", "source": 0},
            {"pattern": "WHERE tbl_name LIKE '********'", "redacted": True},
        ]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_scrub_cache(tracer):
    valueParts1 = [
        {"value": "SELECT * FROM users WHERE password = '"},
        {"value": "1234", "source": 0},
        {"value": ":{SHA1}'"},
    ]
    # valueParts will be modified to be scrubbed, thus these copies
    valueParts1_copy1 = copy.deepcopy(valueParts1)
    valueParts1_copy2 = copy.deepcopy(valueParts1)
    valueParts1_copy3 = copy.deepcopy(valueParts1)
    valueParts2 = [
        {"value": "SELECT * FROM users WHERE password = '"},
        {"value": "123456", "source": 0},
        {"value": ":{SHA1}'"},
    ]

    s1 = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    s2 = Source(origin="SomeOtherOrigin", name="SomeName", value="SomeValue")

    env = {"DD_IAST_REQUEST_SAMPLING": "100", "DD_IAST_ENABLED": "true"}
    with override_env(env):
        oce.reconfigure()
        with tracer.trace("test1") as span:
            oce.acquire_request(span)
            VulnerabilityBase._redacted_report_cache = LFUCache()
            SqlInjection.report(evidence_value=valueParts1, sources=[s1])
            span_report1 = core.get_item(IAST.CONTEXT_KEY, span=span)
            assert span_report1, "no report: check that get_info_frame is not skipping this frame"
            assert list(span_report1.vulnerabilities)[0].evidence == Evidence(
                value=None,
                pattern=None,
                valueParts=[
                    {"value": "SELECT * FROM users WHERE password = '"},
                    {"source": 0, "pattern": "abcd", "redacted": True},
                    {"pattern": "*******'", "redacted": True},
                ],
            )
            assert len(VulnerabilityBase._redacted_report_cache) == 1
        oce.release_request()

        # Should be the same report object
        with tracer.trace("test2") as span:
            oce.acquire_request(span)
            SqlInjection.report(evidence_value=valueParts1_copy1, sources=[s1])
            span_report2 = core.get_item(IAST.CONTEXT_KEY, span=span)
            assert list(span_report2.vulnerabilities)[0].evidence == Evidence(
                value=None,
                pattern=None,
                valueParts=[
                    {"value": "SELECT * FROM users WHERE password = '"},
                    {"source": 0, "pattern": "abcd", "redacted": True},
                    {"pattern": "*******'", "redacted": True},
                ],
            )
            assert id(span_report1) == id(span_report2)
            assert span_report1 is span_report2
            assert len(VulnerabilityBase._redacted_report_cache) == 1
        oce.release_request()

        # Different report, other valueParts
        with tracer.trace("test3") as span:
            oce.acquire_request(span)
            SqlInjection.report(evidence_value=valueParts2, sources=[s1])
            span_report3 = core.get_item(IAST.CONTEXT_KEY, span=span)
            assert list(span_report3.vulnerabilities)[0].evidence == Evidence(
                value=None,
                pattern=None,
                valueParts=[
                    {"value": "SELECT * FROM users WHERE password = '"},
                    {"source": 0, "pattern": "abcdef", "redacted": True},
                    {"pattern": "*******'", "redacted": True},
                ],
            )
            assert id(span_report1) != id(span_report3)
            assert span_report1 is not span_report3
            assert len(VulnerabilityBase._redacted_report_cache) == 2
        oce.release_request()

        # Different report, other source
        with tracer.trace("test4") as span:
            oce.acquire_request(span)
            SqlInjection.report(evidence_value=valueParts1_copy2, sources=[s2])
            span_report4 = core.get_item(IAST.CONTEXT_KEY, span=span)
            assert list(span_report4.vulnerabilities)[0].evidence == Evidence(
                value=None,
                pattern=None,
                valueParts=[
                    {"value": "SELECT * FROM users WHERE password = '"},
                    {"source": 0, "pattern": "abcd", "redacted": True},
                    {"pattern": "*******'", "redacted": True},
                ],
            )
            assert id(span_report1) != id(span_report4)
            assert span_report1 is not span_report4
            assert len(VulnerabilityBase._redacted_report_cache) == 3
        oce.release_request()

        # Same as previous so cache should not increase
        with tracer.trace("test4") as span:
            oce.acquire_request(span)
            SqlInjection.report(evidence_value=valueParts1_copy3, sources=[s2])
            span_report5 = core.get_item(IAST.CONTEXT_KEY, span=span)
            assert list(span_report5.vulnerabilities)[0].evidence == Evidence(
                value=None,
                pattern=None,
                valueParts=[
                    {"value": "SELECT * FROM users WHERE password = '"},
                    {"source": 0, "pattern": "abcd", "redacted": True},
                    {"pattern": "*******'", "redacted": True},
                ],
            )
            assert id(span_report1) != id(span_report5)
            assert span_report1 is not span_report5
            assert id(span_report4) == id(span_report5)
            assert span_report4 is span_report5
            assert len(VulnerabilityBase._redacted_report_cache) == 3
        oce.release_request()
