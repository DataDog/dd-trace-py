import itertools
import os
from pathlib import Path

import pytest

from ddtrace.contrib.internal.azure_servicebus.patch import patch
from ddtrace.contrib.internal.azure_servicebus.patch import unpatch


# Ignoring span link attributes until values are normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
SNAPSHOT_IGNORES = ["meta.messaging.message_id", "meta._dd.span_links"]

METHODS = ["send_messages", "schedule_messages"]
ASYNC_OPTIONS = [False, True]
PAYLOAD_TYPES = ["single", "list", "batch"]
DISTRIBUTED_TRACING_ENABLED_OPTIONS = [None, False]
BATCH_LINKS_ENABLED_OPTIONS = [None, False]


def is_invalid_test_combination(method, payload_type, batch_links_enabled):
    return (method == "schedule_messages" and payload_type == "batch") or (
        payload_type != "batch" and batch_links_enabled is False
    )


params = [
    (
        f"{m}{'_async' if a else ''}_{p}"
        f"_distributed_tracing_{'enabled' if d is None else 'disabled'}"
        f"{'_batch_links_enabled' if p == 'batch' and b is None else '_batch_links_disabled' if p == 'batch' else ''}",
        {
            "METHOD": m,
            "IS_ASYNC": str(a),
            "MESSAGE_PAYLOAD_TYPE": p,
            **({"DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
            **({"DD_TRACE_AZURE_SERVICEBUS_BATCH_LINKS_ENABLED": str(b)} if b is not None else {}),
        },
    )
    for m, a, p, d, b in itertools.product(
        METHODS, ASYNC_OPTIONS, PAYLOAD_TYPES, DISTRIBUTED_TRACING_ENABLED_OPTIONS, BATCH_LINKS_ENABLED_OPTIONS
    )
    if not is_invalid_test_combination(m, p, b)
]

param_ids, param_values = zip(*params)


@pytest.fixture(autouse=True)
def patch_azure_servicebus():
    patch()
    yield
    unpatch()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_vars",
    param_values,
    ids=param_ids,
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
async def test_producer(ddtrace_run_python_code_in_subprocess, env_vars):
    env = os.environ.copy()
    env.update(env_vars)

    helper_path = Path(__file__).resolve().parent.joinpath("common.py")
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(helper_path.read_text(), env=env)

    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()
