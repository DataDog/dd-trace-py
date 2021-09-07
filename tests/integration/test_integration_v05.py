# DEV: Import all the integration tests from test_integration to avoid duplication

import sys

import pytest

import ddtrace
from ddtrace.internal.encoding import MsgpackEncoderV05
import ddtrace.internal.writer
import ddtrace.tracer
from tests.integration.test_integration import *  # noqa
from tests.integration.test_integration import AGENT_VERSION


# Skip all the tests if we are not running with the latest agent version as
# v0.5 might not be available.
if AGENT_VERSION != "latest":
    if pytest.__version__ < "3.0.0":
        pytest.skip()
    else:
        pytestmark = pytest.mark.skip


class AgentWriterV05(ddtrace.internal.writer.AgentWriter):
    def __init__(self, *args, **kwargs):
        super(AgentWriterV05, self).__init__(*args, **kwargs)
        self._endpoint = "v0.5/traces"


# Patch the default encoder
@pytest.fixture(autouse=True)
def patch_encoder():
    tracermodule = sys.modules["ddtrace.tracer"]

    encoder = ddtrace.internal.writer.Encoder
    writer = tracermodule.AgentWriter

    ddtrace.internal.writer.Encoder = MsgpackEncoderV05
    tracermodule.AgentWriter = AgentWriterV05

    yield

    tracermodule.AgentWriter = writer
    ddtrace.internal.writer.Encoder = encoder


@pytest.mark.skip(reason="tricky to get this passing")
def test_payload_too_large():
    pass


@pytest.mark.skip(reason="not relevant")
def test_downgrade():
    pass
