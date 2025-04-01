import os

import pytest

from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.litellm.utils import get_request_vcr
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


def default_global_config():
    return {}


@pytest.fixture
def ddtrace_global_config():
    return {}


@pytest.fixture
def ddtrace_config_litellm():
    return {}


@pytest.fixture
def litellm(ddtrace_global_config, ddtrace_config_litellm):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("litellm", ddtrace_config_litellm):
            with override_env(
                dict(
                    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                    GOOGLE_APPLICATION_CREDENTIALS=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", '{"fake":"credentials"}'),
                )
            ):
                patch()
                import litellm

                yield litellm
                unpatch()


@pytest.fixture
def mock_tracer(litellm):
    pin = Pin.get_from(litellm)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin._override(litellm, tracer=mock_tracer)
    pin.tracer.configure()
    yield mock_tracer


@pytest.fixture
def request_vcr():
    return get_request_vcr()
