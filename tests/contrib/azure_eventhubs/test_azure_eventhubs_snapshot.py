import itertools
import os
from pathlib import Path

from azure.eventhub import EventData
from azure.eventhub import EventHubProducerClient
import pytest

from ddtrace.contrib.internal.azure_eventhubs.patch import patch
from ddtrace.contrib.internal.azure_eventhubs.patch import unpatch


CONNECTION_STRING = "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
EVENTHUB_NAME = "eh1"

# Ignoring span link attributes until values are normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
SNAPSHOT_IGNORES = ["meta.messaging.message_id", "meta._dd.span_links", "meta.error.stack"]

METHODS = ["send_event", "send_batch"]
BUFFERED_OPTIONS = [False, True]
ASYNC_OPTIONS = [False, True]
PAYLOAD_TYPES = ["single", "list", "batch"]
DISTRIBUTED_TRACING_ENABLED_OPTIONS = [None, False]
BATCH_LINKS_ENABLED_OPTIONS = [None, False]


def is_invalid_test_combination(method, buffered_mode, payload_type, distributed_tracing_enabled, batch_links_enabled):
    return (
        # Payloads not valid for method
        (method == "send_event" and payload_type in {"list", "batch"})
        or (method == "send_batch" and payload_type == "single")
        # Only test batch_links config for batches
        or (payload_type != "batch" and batch_links_enabled is False)
        # Only test buffered mode with enabled configs
        or (buffered_mode and (distributed_tracing_enabled is False or batch_links_enabled is False))
    )


params = [
    (
        f"{m}{'_buffered' if b else ''}{'_async' if a else ''}_{p}"
        f"_distributed_tracing_{'enabled' if d is None else 'disabled'}"
        f"{'_batch_links_enabled' if p == 'batch' and bl is None else '_batch_links_disabled' if p == 'batch' else ''}",
        {
            "METHOD": m,
            "BUFFERED_MODE": str(b),
            "IS_ASYNC": str(a),
            "MESSAGE_PAYLOAD_TYPE": p,
            **({"DD_AZURE_EVENTHUBS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
            **({"DD_TRACE_AZURE_EVENTHUBS_BATCH_LINKS_ENABLED": str(bl)} if bl is not None else {}),
        },
    )
    for m, b, a, p, d, bl in itertools.product(
        METHODS,
        BUFFERED_OPTIONS,
        ASYNC_OPTIONS,
        PAYLOAD_TYPES,
        DISTRIBUTED_TRACING_ENABLED_OPTIONS,
        BATCH_LINKS_ENABLED_OPTIONS,
    )
    if not is_invalid_test_combination(m, b, p, d, bl)
]

param_ids, param_values = zip(*params)


@pytest.fixture(autouse=True)
def patch_azure_eventhubs():
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


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_producer_error():
    producer_client = EventHubProducerClient.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME
    )

    try:
        # send_batch does not accept a single EventData
        producer_client.send_batch(EventData(body='{"body":"test message"}'))
    except TypeError as e:
        assert str(e) == "'EventData' object is not iterable"
    finally:
        producer_client.close()
