import os

import mock
import pytest

from ddtrace import Pin
from ddtrace import patch
from ddtrace.contrib.langchain.patch import unpatch
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_config
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def ddtrace_config_langchain():
    return {}


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.llmobs._integrations.base.get_dogstatsd_client")
    try:
        DogStatsdMock = patcher.start()
        m = mock.MagicMock()
        DogStatsdMock.return_value = m
        yield m
    finally:
        patcher.stop()


@pytest.fixture
def mock_logs(scope="session"):
    """
    Note that this fixture must be ordered BEFORE mock_tracer as it needs to patch the log writer
    before it is instantiated.
    """
    patcher = mock.patch("ddtrace.llmobs._integrations.base.V2LogWriter")
    try:
        V2LogWriterMock = patcher.start()
        m = mock.MagicMock()
        V2LogWriterMock.return_value = m
        yield m
    finally:
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
def mock_llmobs_span_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    try:
        LLMObsSpanWriterMock = patcher.start()
        m = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = m
        yield m
    finally:
        patcher.stop()


@pytest.fixture
def langchain(ddtrace_config_langchain, mock_logs, mock_metrics):
    with override_global_config(dict(_dd_api_key="<not-a-real-key>")):
        with override_config("langchain", ddtrace_config_langchain):
            with override_env(
                dict(
                    OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"),
                    COHERE_API_KEY=os.getenv("COHERE_API_KEY", "<not-a-real-key>"),
                    ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "<not-a-real-key>"),
                    HUGGINGFACEHUB_API_TOKEN=os.getenv("HUGGINGFACEHUB_API_TOKEN", "<not-a-real-key>"),
                    AI21_API_KEY=os.getenv("AI21_API_KEY", "<not-a-real-key>"),
                )
            ):
                patch(langchain=True)
                import langchain

                mock_logs.reset_mock()
                mock_metrics.reset_mock()

                yield langchain
                unpatch()


@pytest.fixture
def langchain_community(ddtrace_config_langchain, mock_logs, mock_metrics, langchain):
    try:
        import langchain_community

        yield langchain_community
    except ImportError:
        yield


@pytest.fixture
def langchain_core(ddtrace_config_langchain, mock_logs, mock_metrics, langchain):
    import langchain_core
    import langchain_core.prompts  # noqa: F401

    yield langchain_core


@pytest.fixture
def langchain_openai(ddtrace_config_langchain, mock_logs, mock_metrics, langchain):
    try:
        import langchain_openai

        yield langchain_openai
    except ImportError:
        yield


@pytest.fixture
def langchain_cohere(ddtrace_config_langchain, mock_logs, mock_metrics, langchain):
    try:
        import langchain_cohere

        yield langchain_cohere
    except ImportError:
        yield


@pytest.fixture
def langchain_anthropic(ddtrace_config_langchain, mock_logs, mock_metrics, langchain):
    try:
        import langchain_anthropic

        yield langchain_anthropic
    except ImportError:
        yield
