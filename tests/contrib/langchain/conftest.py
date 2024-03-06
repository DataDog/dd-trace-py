import os

import mock
import pytest

from ddtrace import Pin
from ddtrace.contrib.langchain.patch import patch
from ddtrace.contrib.langchain.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_global_config():
    config = {}
    return config


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>"}


@pytest.fixture
def ddtrace_config_langchain():
    return {}


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.llmobs._integrations.base.get_dogstatsd_client")
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
    patcher = mock.patch("ddtrace.llmobs._integrations.base.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def snapshot_tracer(langchain, mock_logs, mock_metrics):
    pin = Pin.get_from(langchain)
    yield pin.tracer
    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def mock_tracer(langchain, mock_logs, mock_metrics):
    pin = Pin.get_from(langchain)
    mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
    pin.override(langchain, tracer=mock_tracer)
    pin.tracer.configure()
    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def langchain(ddtrace_global_config, ddtrace_config_langchain, mock_logs, mock_metrics):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("langchain", ddtrace_config_langchain):
            # ensure that mock OpenAI API key is passed in
            os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
            os.environ["COHERE_API_KEY"] = os.getenv("COHERE_API_KEY", "<not-a-real-key>")
            os.environ["HUGGINGFACEHUB_API_TOKEN"] = os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>")
            os.environ["AI21_API_KEY"] = os.getenv("AI21_API_KEY", "<not-a-real-key>")
            patch()
            import langchain

            yield langchain
            unpatch()


@pytest.fixture
def langchain_community(ddtrace_global_config, ddtrace_config_langchain, mock_logs, mock_metrics):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("langchain", ddtrace_config_langchain):
            # ensure that mock OpenAI API key is passed in
            os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
            os.environ["COHERE_API_KEY"] = os.getenv("COHERE_API_KEY", "<not-a-real-key>")
            os.environ["HUGGINGFACEHUB_API_TOKEN"] = os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>")
            os.environ["AI21_API_KEY"] = os.getenv("AI21_API_KEY", "<not-a-real-key>")
            patch()
            import langchain_community

            yield langchain_community
            unpatch()


@pytest.fixture
def langchain_openai(ddtrace_global_config, ddtrace_config_langchain, mock_logs, mock_metrics):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        with override_config("langchain", ddtrace_config_langchain):
            # ensure that mock OpenAI API key is passed in
            os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "<not-a-real-key>")
            os.environ["COHERE_API_KEY"] = os.getenv("COHERE_API_KEY", "<not-a-real-key>")
            os.environ["HUGGINGFACEHUB_API_TOKEN"] = os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>")
            os.environ["AI21_API_KEY"] = os.getenv("AI21_API_KEY", "<not-a-real-key>")
            patch()
            try:
                import langchain_openai

                yield langchain_openai
            except ImportError:
                import langchain_community

                yield langchain_community
            finally:
                unpatch()
