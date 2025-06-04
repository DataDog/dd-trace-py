import logging
from time import sleep

import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast.sampling.vulnerability_detection import reset_request_vulnerabilities
from ddtrace.settings.asm import config as asm_config
from tests.utils import override_global_config


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


@pytest.mark.skip_iast_check_logs
def test_oce_max_vulnerabilities_per_request(iast_context_deduplication_enabled):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    m.digest()
    span_report = get_iast_reporter()

    assert len(span_report.vulnerabilities) == asm_config._iast_max_vulnerabilities_per_requests


@pytest.mark.skip_iast_check_logs
def test_oce_reset_vulnerabilities_report(iast_context_deduplication_enabled):
    import hashlib

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    m.digest()
    m.digest()
    m.digest()
    reset_request_vulnerabilities()
    m.digest()

    span_report = get_iast_reporter()

    assert len(span_report.vulnerabilities) == asm_config._iast_max_vulnerabilities_per_requests + 1


@pytest.mark.skip_iast_check_logs
def test_oce_no_race_conditions_in_span(iast_span_defaults):
    from ddtrace.appsec._iast._overhead_control_engine import OverheadControl

    oc = OverheadControl()
    oc.reconfigure()

    assert oc._request_quota == asm_config._iast_max_concurrent_requests

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


def acquire_and_release_quota_in_spans(oc, iast_span_defaults):
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


@pytest.mark.skip_iast_check_logs
def test_oce_concurrent_requests_in_spans(iast_span_defaults):
    """
    Ensures quota is always within bounds after multithreading scenario
    """
    import threading

    from ddtrace.appsec._iast._overhead_control_engine import OverheadControl

    oc = OverheadControl()
    oc.reconfigure()

    results = []
    num_requests = 5000

    threads = [
        threading.Thread(target=acquire_and_release_quota_in_spans, args=(oc, iast_span_defaults))
        for _ in range(0, num_requests)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        results.append(thread.join())

    # Ensures quota is always within bounds after multithreading scenario
    assert 0 <= oc._request_quota <= asm_config._iast_max_concurrent_requests


@pytest.mark.skip_iast_check_logs
def test_oce_concurrent_requests_futures_in_spans(tracer, iast_span_defaults, caplog):
    import concurrent.futures

    results = []
    num_requests = 5
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(
        logging.DEBUG
    ), concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for _ in range(0, num_requests):
            futures.append(executor.submit(function_with_vulnerabilities_1, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_2, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_3, tracer))

        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    span_report = get_iast_reporter()

    assert len(span_report.vulnerabilities)

    assert len(results) == num_requests * 3
    assert len(span_report.vulnerabilities) >= 1
