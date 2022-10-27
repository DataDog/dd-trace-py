import sys
from time import sleep

import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast.overhead_control_engine import MAX_REQUESTS
from ddtrace.appsec.iast.overhead_control_engine import MAX_VULNERABILITIES_PER_REQUEST
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.internal import _context


def function_with_vulnerabilities_3(tracer):
    with tracer.trace("test_child"):
        import hashlib

        m = hashlib.md5()
        m.update(b"Nobody inspects")
        m.digest()
        sleep(0.3)
    return 1


def function_with_vulnerabilities_2(tracer):
    with tracer.trace("test_child"):
        import hashlib

        m = hashlib.md5()
        m.update(b"Nobody inspects")
        m.digest()
        sleep(0.2)
    return 1


def function_with_vulnerabilities_1(tracer):
    with tracer.trace("test_child"):
        import hashlib

        m = hashlib.md5()
        m.update(b"Nobody inspects")
        m.digest()
        sleep(0.1)
    return 1


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


def test_oce_max_requests(tracer, iast_span):
    import threading

    results = []
    num_requests = 5
    total_vulnerabilities = 0

    threads = [threading.Thread(target=function_with_vulnerabilities_1, args=(tracer,)) for _ in range(0, num_requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        results.append(thread.join())

    spans = tracer.pop()
    for span in spans:
        span_report = _context.get_item(IAST_CONTEXT_KEY, span=span)
        if span_report:
            total_vulnerabilities += len(span_report.vulnerabilities)

    assert len(results) == num_requests
    assert len(spans) == num_requests
    assert total_vulnerabilities == 1


@pytest.mark.skipif(sys.version_info < (3, 0, 0), reason="concurrent.futures exists in Python 3")
def test_oce_max_requests_py3(tracer, iast_span):
    import concurrent.futures

    results = []
    num_requests = 5
    total_vulnerabilities = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for _ in range(0, num_requests):
            futures.append(executor.submit(function_with_vulnerabilities_1, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_2, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_3, tracer))

        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    spans = tracer.pop()
    for span in spans:
        span_report = _context.get_item(IAST_CONTEXT_KEY, span=span)
        if span_report:
            total_vulnerabilities += len(span_report.vulnerabilities)

    assert len(results) == num_requests * 3
    assert len(spans) == num_requests * 3
    assert total_vulnerabilities == MAX_REQUESTS
