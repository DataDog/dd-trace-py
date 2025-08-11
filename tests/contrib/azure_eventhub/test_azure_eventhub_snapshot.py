import itertools
import os
from pathlib import Path

import pytest

from ddtrace.contrib.internal.azure_eventhub.patch import patch
from ddtrace.contrib.internal.azure_eventhub.patch import unpatch


distributed_tracing_enabled_options = [False, True]
async_options = [False, True]
batch_options = [False, True]

param_values = list(itertools.product(distributed_tracing_enabled_options, async_options, batch_options))

param_ids = [
    f"{'distributed_tracing_enabled' if distributed_tracing_enabled else 'distributed_tracing_disabled'}_"
    f"{'async' if is_async else 'sync'}_"
    f"{'batch' if is_batch else 'send_event'}"
    for distributed_tracing_enabled, is_async, is_batch in param_values
]


@pytest.fixture(autouse=True)
def patch_azure_eventhub():
    patch()
    yield
    unpatch()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "distributed_tracing_enabled, is_async, is_batch",
    param_values,
    ids=param_ids,
)
@pytest.mark.snapshot
async def test_producer(ddtrace_run_python_code_in_subprocess, distributed_tracing_enabled, is_async, is_batch):
    env = os.environ.copy()
    env_vars = {
        "DD_AZURE_EVENTHUB_DISTRIBUTED_TRACING": str(distributed_tracing_enabled),
        "IS_ASYNC": str(is_async),
        "IS_BATCH": str(is_batch),
    }
    env.update(env_vars)

    helper_path = Path(__file__).resolve().parent.joinpath("common.py")
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(helper_path.read_text(), env=env)

    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()
