import os

import pytest

from ddtrace import Pin
from ddtrace.contrib.anthropic.patch import patch
from ddtrace.contrib.anthropic.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_anthropic():
    return {}


@pytest.fixture
def snapshot_tracer(anthropic):
    pin = Pin.get_from(anthropic)
    yield pin.tracer


@pytest.fixture
def mock_tracer(anthropic):
    pin = Pin.get_from(anthropic)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin.override(anthropic, tracer=mock_tracer)
    pin.tracer.configure()
    yield mock_tracer


@pytest.fixture
def anthropic(ddtrace_config_anthropic):
    with override_global_config({"_dd_api_key": "<not-a-real-api-key>"}):
        with override_config("anthropic", ddtrace_config_anthropic):
            with override_env(
                dict(
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch()
                import anthropic

                yield anthropic
                unpatch()
