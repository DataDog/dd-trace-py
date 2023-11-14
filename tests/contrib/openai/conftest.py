import os
import sys
from typing import TYPE_CHECKING
from typing import List
from typing import Optional

import mock
import pytest

from ddtrace import Pin
from ddtrace import patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.filters import TraceFilter
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config


if TYPE_CHECKING:
    from ddtrace import Span


def pytest_configure(config):
    config.addinivalue_line("markers", "vcr_logs(*args, **kwargs): mark test to have logs requests recorded")


@pytest.fixture
def api_key_in_env():
    return True


@pytest.fixture
def request_api_key(api_key_in_env, openai_api_key):
    """
    OpenAI allows both using an env var or a specified param for the API key, so this fixture specifies the API key
    (or None) to be used in the actual request param. If the API key is set as an env var, this should return None
    to make sure the env var will be used.
    """
    if api_key_in_env:
        return None
    return openai_api_key


@pytest.fixture
def openai_api_key():
    return os.getenv("OPENAI_API_KEY", "<not-a-real-key>")


@pytest.fixture
def openai_organization():
    return None


@pytest.fixture
def openai(openai_api_key, openai_organization, api_key_in_env):
    import openai

    if api_key_in_env:
        openai.api_key = openai_api_key
    # When testing locally to generate new cassette files, comment the line below to use the real OpenAI API key.
    os.environ["OPENAI_API_KEY"] = "<not-a-real-key>"
    openai.organization = openai_organization
    yield openai
    # Since unpatching doesn't work (see the unpatch() function),
    # wipe out all the OpenAI modules so that state is reset for each test case.
    mods = list(k for k in sys.modules.keys() if k.startswith("openai"))
    for m in mods:
        del sys.modules[m]


@pytest.fixture
def azure_openai(openai):
    openai.api_type = "azure"
    openai.api_version = "2023-05-15"
    openai.api_base = "https://test-openai.openai.azure.com/"
    yield openai


@pytest.fixture
def azure_openai_config(openai):
    config = {
        "api_version": "2023-07-01-preview",
        "azure_endpoint": "https://test-openai.openai.azure.com/",
        "azure_deployment": "test-openai",
        "api_key": "<not-a-real-key>",
    }
    return config


class FilterOrg(TraceFilter):
    """Replace the organization tag on spans with fake data."""

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        for span in trace:
            if span.get_tag("organization"):
                span.set_tag_str("organization", "not-a-real-org")
        return trace


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.contrib._trace_utils_llm.get_dogstatsd_client")
    DogStatsdMock = patcher.start()
    m = mock.MagicMock()
    DogStatsdMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_logs(scope="session"):
    """
    Note that this fixture must be ordered BEFORE mock_tracer as it needs to patch the log writer
    before it is instantiated.
    """
    patcher = mock.patch("ddtrace.contrib._trace_utils_llm.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def ddtrace_config_openai():
    config = {}
    return config


@pytest.fixture
def patch_openai(ddtrace_config_openai, openai_api_key, openai_organization, api_key_in_env):
    with override_config("openai", ddtrace_config_openai):
        if api_key_in_env:
            openai.api_key = openai_api_key
        openai.organization = openai_organization
        patch(openai=True)
        yield
        unpatch()


@pytest.fixture
def snapshot_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield pin.tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def mock_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin.override(openai, tracer=mock_tracer)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()
