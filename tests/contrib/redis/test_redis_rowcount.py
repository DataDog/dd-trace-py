import warnings

import pytest

from ddtrace.contrib.internal.redis import patch as redis_patch
from ddtrace.contrib.internal.valkey import patch as valkey_patch


@pytest.mark.parametrize(
    "run, command_kwarg",
    [
        (redis_patch._run_redis_command, "redis"),
        (valkey_patch._run_valkey_command, "valkey"),
    ],
)
@pytest.mark.parametrize(
    "args, result, expected",
    [
        (("INCR", "k"), 1, None),
        (("SET", "k", "v"), b"OK", None),
        (("GET", "k"), b"v", 1),
        (("GET", "k"), None, 0),
    ],
)
def test_rowcount_does_not_compare_non_row_results(run, command_kwarg, args, result, expected):
    """Non row-returning commands must not compare the result with bytes (BytesWarning under ``python -b``)."""
    calls = []
    module = redis_patch if command_kwarg == "redis" else valkey_patch
    original = module.core.dispatch
    module.core.dispatch = lambda name, payload: calls.append(payload)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", BytesWarning)
            run(None, lambda *a, **k: result, args, {})
    finally:
        module.core.dispatch = original
    assert calls == [(None, expected)]
