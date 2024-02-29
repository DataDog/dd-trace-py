from time import sleep

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._overhead_control_engine import MAX_REQUESTS
from ddtrace.appsec._iast._overhead_control_engine import MAX_VULNERABILITIES_PER_REQUEST
from ddtrace.internal import core


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


def test_oce_max_vulnerabilities_per_request(iast_span_defaults):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    m.digest()
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert len(span_report.vulnerabilities) == MAX_VULNERABILITIES_PER_REQUEST


def test_oce_reset_vulnerabilities_report(iast_span_defaults):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    oce.vulnerabilities_reset_quota()
    m.digest()

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    assert len(span_report.vulnerabilities) == MAX_VULNERABILITIES_PER_REQUEST + 1


def test_oce_no_race_conditions(tracer, iast_span_defaults):
    from ddtrace.appsec._iast._overhead_control_engine import OverheadControl

    oc = OverheadControl()
    oc.reconfigure()

    assert oc._request_quota == MAX_REQUESTS

    # Request 1 tries to acquire the lock
    assert oc.acquire_request(iast_span_defaults) is True

    # oce should have quota
    assert oc._request_quota > 0

    # Request 2 tries to acquire the lock
    assert oc.acquire_request(iast_span_defaults) is True

    # oce should not have quota
    assert oc._request_quota == 0

    # Request 3 tries to acquire the lock and fails
    assert oc.acquire_request(iast_span_defaults) is False

    # oce should have quota
    assert oc._request_quota == 0

    # Request 1 releases the lock
    oc.release_request()

    assert oc._request_quota > 0

    # Request 4 tries to acquire the lock
    assert oc.acquire_request(iast_span_defaults) is True

    # oce should have quota
    assert oc._request_quota == 0

    # Request 4 releases the lock
    oc.release_request()

    # oce should have quota again
    assert oc._request_quota > 0

    # Request 5 tries to acquire the lock
    assert oc.acquire_request(iast_span_defaults) is True

    # oce should not have quota
    assert oc._request_quota == 0


def acquire_and_release_quota(oc, iast_span_defaults):
    """
    Just acquires the request quota and releases it with some
    random sleeps
    """
    import random
    import time

    random_int = random.randint(1, 10)
    time.sleep(0.01 * random_int)
    if oc.acquire_request(iast_span_defaults):
        time.sleep(0.01 * random_int)
        oc.release_request()


def test_oce_concurrent_requests(tracer, iast_span_defaults):
    """
    Ensures quota is always within bounds after multithreading scenario
    """
    import threading

    from ddtrace.appsec._iast._overhead_control_engine import MAX_REQUESTS
    from ddtrace.appsec._iast._overhead_control_engine import OverheadControl

    oc = OverheadControl()
    oc.reconfigure()

    results = []
    num_requests = 5000

    threads = [
        threading.Thread(target=acquire_and_release_quota, args=(oc, iast_span_defaults))
        for _ in range(0, num_requests)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        results.append(thread.join())

    # Ensures quota is always within bounds after multithreading scenario
    assert 0 <= oc._request_quota <= MAX_REQUESTS
