import os

import mock
import pytest

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from tests.llmobs._utils import logs_vcr
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config
from tests.utils import request_token


@pytest.fixture(autouse=True)
def vcr_logs(request):
    marks = [m for m in request.node.iter_markers(name="vcr_logs")]
    assert len(marks) < 2
    if marks:
        mark = marks[0]
        cass = mark.kwargs.get("cassette", request_token(request).replace(" ", "_").replace(os.path.sep, "_"))
        with logs_vcr.use_cassette("%s.yaml" % cass):
            yield
    else:
        yield


def pytest_configure(config):
    config.addinivalue_line("markers", "vcr_logs: mark test to use recorded request/responses")


@pytest.fixture
def mock_llmobs_span_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    LLMObsSpanWriterMock = patcher.start()
    m = mock.MagicMock()
    LLMObsSpanWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_llmobs_span_agentless_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
    LLMObsSpanWriterMock = patcher.start()
    m = mock.MagicMock()
    LLMObsSpanWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_llmobs_eval_metric_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsEvalMetricWriter")
    LLMObsEvalMetricWriterMock = patcher.start()
    m = mock.MagicMock()
    LLMObsEvalMetricWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_llmobs_evaluator_runner():
    patcher = mock.patch("ddtrace.llmobs._llmobs.EvaluatorRunner")
    LLMObsEvalRunner = patcher.start()
    m = mock.MagicMock()
    LLMObsEvalRunner.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_llmobs_submit_evaluation():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObs.submit_evaluation")
    LLMObsMock = patcher.start()
    m = mock.MagicMock()
    LLMObsMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_http_writer_send_payload_response():
    with mock.patch(
        "ddtrace.internal.writer.HTTPWriter._send_payload",
        return_value=Response(
            status=200,
            body="{}",
        ),
    ):
        yield


@pytest.fixture
def mock_http_writer_put_response_forbidden():
    with mock.patch(
        "ddtrace.internal.writer.HTTPWriter._put",
        return_value=Response(
            status=403,
            reason=b'{"errors":[{"status":"403","title":"Forbidden","detail":"API key is invalid"}]}',
        ),
    ):
        yield


@pytest.fixture
def mock_writer_logs():
    with mock.patch("ddtrace.llmobs._writer.logger") as m:
        yield m


@pytest.fixture
def mock_evaluator_logs():
    with mock.patch("ddtrace.llmobs._evaluators.runner.logger") as m:
        yield m


@pytest.fixture
def mock_evaluator_sampler_logs():
    with mock.patch("ddtrace.llmobs._evaluators.sampler.logger") as m:
        yield m


@pytest.fixture
def mock_http_writer_logs():
    with mock.patch("ddtrace.internal.writer.writer.log") as m:
        yield m


@pytest.fixture
def ddtrace_global_config():
    config = {}
    return config


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "unnamed-ml-app"}


@pytest.fixture
def LLMObs(
    mock_llmobs_span_writer, mock_llmobs_eval_metric_writer, mock_llmobs_evaluator_runner, ddtrace_global_config
):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    with override_global_config(global_config):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        yield llmobs_service
        llmobs_service.disable()


@pytest.fixture
def AgentlessLLMObs(
    mock_llmobs_span_agentless_writer,
    mock_llmobs_eval_metric_writer,
    mock_llmobs_evaluator_runner,
    ddtrace_global_config,
):
    global_config = default_global_config()
    global_config.update(ddtrace_global_config)
    global_config.update(dict(_llmobs_agentless_enabled=True))
    with override_global_config(global_config):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        yield llmobs_service
        llmobs_service.disable()


@pytest.fixture
def disabled_llmobs():
    prev = llmobs_service.enabled
    llmobs_service.enabled = False
    yield
    llmobs_service.enabled = prev


@pytest.fixture
def mock_ragas_dependencies_not_present():
    import ragas

    previous = ragas.__version__
    ## unsupported version
    ragas.__version__ = "0.0.0"
    yield
    ragas.__version__ = previous


@pytest.fixture
def ragas(mock_llmobs_span_writer, mock_llmobs_eval_metric_writer):
    with override_global_config(dict(_dd_api_key="<not-a-real-key>")):
        import ragas

        with override_env(dict(OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"))):
            yield ragas


@pytest.fixture
def reset_ragas_faithfulness_llm():
    import ragas

    previous_llm = ragas.metrics.faithfulness.llm
    yield
    ragas.metrics.faithfulness.llm = previous_llm


@pytest.fixture
def mock_ragas_evaluator(mock_llmobs_eval_metric_writer, ragas):
    patcher = mock.patch("ddtrace.llmobs._evaluators.ragas.faithfulness.RagasFaithfulnessEvaluator.evaluate")
    LLMObsMockRagas = patcher.start()
    LLMObsMockRagas.return_value = 1.0
    yield RagasFaithfulnessEvaluator
    patcher.stop()
