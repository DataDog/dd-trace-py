import pytest
import temporalio
from temporalio.client import Client

from ddtrace.contrib.internal.temporalio.patch import _supported_versions
from ddtrace.contrib.internal.temporalio.patch import get_version
from ddtrace.contrib.internal.temporalio.patch import patch
from ddtrace.contrib.internal.temporalio.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.wrapping import get_wrapped
from ddtrace.internal.wrapping import is_wrapped


def _is_wrapped(obj: object) -> bool:
    return is_wrapted(obj) or is_wrapped(obj)


def _wrapped_object(obj: object) -> object:
    return obj.__wrapped__ if is_wrapted(obj) else get_wrapped(obj)


def test_version_reporting() -> None:
    assert get_version() == temporalio.__version__
    assert _supported_versions() == {"temporalio": ">=1.0.0"}


def test_patch_wraps_client_construction() -> None:
    unpatch()
    assert not _is_wrapped(Client.__init__)

    patch()

    assert _is_wrapped(Client.__init__)


def test_patch_is_idempotent() -> None:
    patch()
    patch()

    assert _is_wrapped(Client.__init__)
    assert not _is_wrapped(_wrapped_object(Client.__init__))


def test_unpatch_restores_client_construction() -> None:
    patch()
    assert _is_wrapped(Client.__init__)

    unpatch()

    assert not _is_wrapped(Client.__init__)


@pytest.mark.subprocess(env={"DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v1"})
def test_schema_v1_service_and_operation_names() -> None:
    import importlib

    from ddtrace import config
    from ddtrace.contrib._events.temporalio import TemporalQueryWorkflowEvent
    from ddtrace.contrib._events.temporalio import TemporalRunActivityEvent
    from ddtrace.contrib._events.temporalio import TemporalStartWorkflowEvent
    from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME

    importlib.import_module("ddtrace.contrib.internal.temporalio.patch")
    event_kwargs = {"component": "temporalio", "integration_config": config.temporalio}
    assert TemporalStartWorkflowEvent(**event_kwargs).operation_name == "temporal.send"
    assert TemporalRunActivityEvent(**event_kwargs).operation_name == "temporal.process"
    assert TemporalQueryWorkflowEvent(**event_kwargs).operation_name == "temporal.client.request"
    assert config.temporalio._default_service == DEFAULT_SPAN_SERVICE_NAME


@pytest.mark.subprocess(env={"DD_TEMPORALIO_DISTRIBUTED_TRACING": "false"})
def test_distributed_tracing_environment_configuration() -> None:
    import importlib

    from ddtrace import config

    importlib.import_module("ddtrace.contrib.internal.temporalio.patch")
    assert config.temporalio.distributed_tracing is False
