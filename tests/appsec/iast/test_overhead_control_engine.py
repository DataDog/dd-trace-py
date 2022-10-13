import sys

import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.overhead_control_engine import MAX_VULNERABILITIES_PER_REQUEST
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.internal import _context


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="digest works only in Python 3")
def test_oce_max_vulnerabilities_per_request(iast_span):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    m.digest()
    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert len(span_report.vulnerabilities) == MAX_VULNERABILITIES_PER_REQUEST


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="digest works only in Python 3")
def test_oce_reset_vulnerabilities_report(iast_span):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    oce.vulnerabilities_reset_quota()
    m.digest()

    span_report = _context.get_item(IAST_CONTEXT_KEY, span=iast_span)

    assert len(span_report.vulnerabilities) == MAX_VULNERABILITIES_PER_REQUEST + 1
