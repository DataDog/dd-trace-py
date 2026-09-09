import itertools

import pytest

from tests.webclient import Client


# Ignoring span link attributes until values are normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
SNAPSHOT_IGNORES = ["meta.messaging.message_id", "span_links.tracestate", "span_links.trace_id_high"]
DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ENTITY_TYPES = ["queue", "topic"]
ASYNC_OPTIONS = [False, True]
CARDINALITY = ["one", "many"]
DISTRIBUTED_TRACING_ENABLED_OPTIONS = [None, False]

params = [
    (
        f"{e}{'_async' if a else ''}_consume_{c}_distributed_tracing_{'enabled' if d is None else 'disabled'}",
        (
            {
                "IS_ASYNC": str(a),
                "CARDINALITY": c,
                **({"DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
                **({"DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
            },
            e,
            "single" if c == "one" else "batch",
        ),
    )
    for e, a, c, d in itertools.product(ENTITY_TYPES, ASYNC_OPTIONS, CARDINALITY, DISTRIBUTED_TRACING_ENABLED_OPTIONS)
]

param_ids, param_values = zip(*params)


@pytest.mark.parametrize(
    "azure_functions_client, entity, payload_type",
    param_values,
    ids=param_ids,
    indirect=["azure_functions_client"],
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_service_bus_trigger(azure_functions_client: Client, entity: str, payload_type: str) -> None:
    assert (
        azure_functions_client.post(f"/api/{entity}sendmessage{payload_type}", headers=DEFAULT_HEADERS).status_code
        == 200
    )
