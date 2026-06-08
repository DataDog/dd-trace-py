import pytest

from ddtrace.contrib.internal.protobuf.patch import patch
from ddtrace.contrib.internal.protobuf.patch import unpatch
from tests.utils import override_global_config


def default_global_config():
    return {"_data_streams_enabled": True}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def protobuf(ddtrace_global_config):
    from ddtrace.internal.datastreams import data_streams_processor

    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        patch()
        # The schema sampler is a process-global singleton keyed by the generic
        # operation type ("serialization"/"deserialization"), shared across schemas,
        # and only samples a key once per 30s window. Clearing its state per test
        # ensures every test samples its schema independently, regardless of the
        # order tests run in (pytest-randomly) — otherwise the `schema.definition`
        # tag is dropped for tests that aren't first-in-window for their key.
        data_streams_processor()._schema_samplers.clear()
        from google import protobuf

        yield protobuf
        unpatch()
