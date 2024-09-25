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
    from ddtrace.internal.datastreams.schemas.schema_sampler import SchemaSampler

    SchemaSampler.SAMPLE_INTERVAL_MILLIS = 0  # change to ensure we sample each schema

    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        patch()
        from google import protobuf

        yield protobuf
        unpatch()
        SchemaSampler.SAMPLE_INTERVAL_MILLIS = 30000
