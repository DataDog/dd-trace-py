import pytest

from ddtrace._trace.pin import Pin
import ddtrace.contrib  # noqa: F401
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_ACTIVITY_TRIGGER
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_ENTITY_TRIGGER
from ddtrace.contrib.internal.azure_durable_functions.patch import _DURABLE_TRIGGER_DEFS
from ddtrace.contrib.internal.azure_durable_functions.patch import _wrap_durable_trigger


SNAPSHOT_IGNORES = [
    "duration",
    "start",
    "error",
    "meta._dd.p.dm",
    "meta._dd.p.tid",
    "meta._dd.base_service",
    "meta.runtime-id",
    "metrics.process_id",
]


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_activity_trigger():
    def activity():
        return "ok"

    trigger_name, context_name = _DURABLE_TRIGGER_DEFS[_DURABLE_ACTIVITY_TRIGGER]
    wrapped = _wrap_durable_trigger(Pin(), activity, "sample_activity", trigger_name, context_name)
    assert wrapped() == "ok"


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_entity_trigger():
    def entity():
        return "ok"

    trigger_name, context_name = _DURABLE_TRIGGER_DEFS[_DURABLE_ENTITY_TRIGGER]
    wrapped = _wrap_durable_trigger(Pin(), entity, "sample_entity", trigger_name, context_name)
    assert wrapped() == "ok"
