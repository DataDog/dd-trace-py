from time import sleep

from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._taint_tracking._context import clear_all_request_context_slots
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.sampling.vulnerability_detection import reset_request_vulnerabilities
from ddtrace.appsec._iast.taint_sinks.weak_hash import WeakHash
from ddtrace.internal.settings.asm import config as asm_config


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


def test_oce_max_vulnerabilities_per_request(iast_context_deduplication_enabled):
    import hashlib

    # Reset deduplication cache to ensure clean state
    WeakHash._prepare_report._reset_cache()

    # Verify IAST context is enabled
    assert is_iast_request_enabled(), "IAST request context should be enabled"

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    # Each digest() call must be on a different line to avoid deduplication
    result1 = m.digest()  # vulnerability 1
    result2 = m.digest()  # vulnerability 2
    result3 = m.digest()  # This should not be reported (exceeds max)
    result4 = m.digest()  # This should not be reported (exceeds max)

    # Ensure all digest calls completed
    assert result1 is not None and result2 is not None and result3 is not None and result4 is not None

    span_report = get_iast_reporter()
    if span_report is None:
        # Debug: check if any vulnerabilities were attempted
        env = _get_iast_env()
        if env:
            print(
                f"DEBUG: vulnerability_budget={env.vulnerability_budget}, "
                f"vulnerabilities_request_limit={env.vulnerabilities_request_limit}"
            )
        assert False, (
            f"IAST reporter should be initialized after vulnerability detection. "
            f"IAST enabled: {is_iast_request_enabled()}, env: {env is not None}"
        )

    assert len(span_report.vulnerabilities) == asm_config._iast_max_vulnerabilities_per_requests


def test_oce_reset_vulnerabilities_report(iast_context_deduplication_enabled):
    import hashlib

    # Reset deduplication cache to ensure clean state
    WeakHash._prepare_report._reset_cache()

    # Verify IAST context is enabled
    assert is_iast_request_enabled(), "IAST request context should be enabled"

    m = hashlib.md5()
    m.update(b"Nobody inspects")
    # Each digest() call must be on a different line to avoid deduplication
    result1 = m.digest()  # vulnerability 1
    result2 = m.digest()  # vulnerability 2
    result3 = m.digest()  # This should not be reported (exceeds max)

    # Ensure all digest calls completed
    assert result1 is not None and result2 is not None and result3 is not None

    # Ensure reporter exists before reset
    span_report = get_iast_reporter()
    if span_report is None:
        # Debug: check if any vulnerabilities were attempted
        env = _get_iast_env()
        if env:
            print(
                f"DEBUG: vulnerability_budget={env.vulnerability_budget}, "
                f"vulnerabilities_request_limit={env.vulnerabilities_request_limit}"
            )
        assert False, (
            f"IAST reporter should exist before reset. IAST enabled: {is_iast_request_enabled()}, "
            "env: {env is not None}"
        )

    initial_count = len(span_report.vulnerabilities)
    assert initial_count == asm_config._iast_max_vulnerabilities_per_requests

    reset_request_vulnerabilities()
    result4 = m.digest()  # vulnerability 3 (after reset)
    assert result4 is not None

    span_report = get_iast_reporter()
    assert span_report is not None, "IAST reporter should still exist after reset"
    # After reset, we should have the original 2 vulnerabilities + 1 new one = 3 total
    assert len(span_report.vulnerabilities) == asm_config._iast_max_vulnerabilities_per_requests + 1


def test_oce_no_race_conditions_in_span(iast_span_defaults):
    """
    Validate that acquiring and releasing request contexts through the
    TaintEngineContext respects capacity and has no race conditions in a
    sequential scenario. This replaces the OverheadControl quota test.
    """
    clear_all_request_context_slots()

    capacity = debug_context_array_size()
    assert capacity >= 1

    free_initial = debug_context_array_free_slots_number()
    # Ensure we start from a clean state
    assert free_initial == capacity

    # Acquire two contexts if possible
    ctx_id_1 = start_request_context()
    assert isinstance(ctx_id_1, int)
    ctx_id_2 = start_request_context()
    if capacity >= 2:
        assert isinstance(ctx_id_2, int)
        # After two acquisitions, free slots must be capacity - 2
        assert debug_context_array_free_slots_number() == max(0, capacity - 2)
    else:
        # Only one slot capacity
        assert ctx_id_2 is None
        assert debug_context_array_free_slots_number() == max(0, capacity - 1)

    # Try to acquire one more than capacity to ensure it fails when full
    acquired = [ctx_id_1]
    if isinstance(ctx_id_2, int):
        acquired.append(ctx_id_2)
    # Fill the remaining capacity
    while len(acquired) < capacity:
        cid = start_request_context()
        assert isinstance(cid, int)
        acquired.append(cid)

    # Pool is full now
    assert debug_context_array_free_slots_number() == 0
    assert start_request_context() is None

    # Release one, verify a free slot appears and can be reused
    finish_request_context(acquired[0])
    assert debug_context_array_free_slots_number() == 1
    new_id = start_request_context()
    assert isinstance(new_id, int)
    assert debug_context_array_free_slots_number() == 0

    # Cleanup
    for cid in acquired[1:]:
        finish_request_context(cid)
    finish_request_context(new_id)
    assert debug_context_array_free_slots_number() == capacity


def acquire_and_release_context_slot():
    """Acquire a native request context slot, sleep briefly, then release it."""
    import random
    import time

    random_int = random.randint(1, 10)
    time.sleep(0.001 * random_int)
    ctx_id = start_request_context()
    if isinstance(ctx_id, int):
        time.sleep(0.001 * random_int)
        finish_request_context(ctx_id)


def test_oce_concurrent_requests_in_spans(iast_span_defaults):
    """Ensure free slots stay within bounds after a multithreading scenario."""
    import threading

    clear_all_request_context_slots()

    capacity = debug_context_array_size()
    assert debug_context_array_free_slots_number() == capacity

    num_requests = 5000

    threads = [threading.Thread(target=acquire_and_release_context_slot) for _ in range(0, num_requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # After all threads complete, all slots must be free again
    assert debug_context_array_free_slots_number() == capacity


def test_oce_concurrent_requests_futures_in_spans(tracer, iast_span_defaults, caplog):
    import concurrent.futures

    results = []
    num_requests = 5
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for _ in range(0, num_requests):
            futures.append(executor.submit(function_with_vulnerabilities_1, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_2, tracer))
            futures.append(executor.submit(function_with_vulnerabilities_3, tracer))

        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    span_report = get_iast_reporter()
    assert len(results) == num_requests * 3
    assert span_report is None
