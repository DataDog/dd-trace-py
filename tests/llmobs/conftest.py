import os

import mock
import pytest

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from ddtrace.llmobs._writer import LLMObsSpanWriter
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
        return_value=Response(status=200, body="{}"),
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
def mock_llmobs_logs():
    with mock.patch("ddtrace.llmobs._llmobs.log") as m:
        yield m
        m.reset_mock()


@pytest.fixture
def ddtrace_global_config():
    config = {}
    return config


def default_global_config():
    return {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "unnamed-ml-app", "service": "tests.llmobs"}


@pytest.fixture
def mock_ragas_dependencies_not_present():
    import ragas

    previous = ragas.__version__
    ## unsupported version
    ragas.__version__ = "0.0.0"
    yield
    ragas.__version__ = previous


@pytest.fixture
def ragas(mock_llmobs_eval_metric_writer):
    with override_global_config(dict(_dd_api_key="<not-a-real-key>")):
        try:
            import ragas
        except ImportError:
            pytest.skip("Ragas not installed")
        with override_env(dict(OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "<not-a-real-key>"))):
            yield ragas


@pytest.fixture
def reset_ragas_faithfulness_llm():
    try:
        import ragas
    except ImportError:
        pytest.skip("Ragas not installed")
    previous_llm = ragas.metrics.faithfulness.llm
    yield
    ragas.metrics.faithfulness.llm = previous_llm


@pytest.fixture
def reset_ragas_answer_relevancy_llm():
    import ragas

    previous_llm = ragas.metrics.answer_relevancy.llm
    yield
    ragas.metrics.answer_relevancy.llm = previous_llm


@pytest.fixture
def mock_ragas_evaluator(mock_llmobs_eval_metric_writer, ragas):
    patcher = mock.patch("ddtrace.llmobs._evaluators.ragas.faithfulness.RagasFaithfulnessEvaluator.evaluate")
    LLMObsMockRagas = patcher.start()
    LLMObsMockRagas.return_value = 1.0
    yield RagasFaithfulnessEvaluator
    patcher.stop()


@pytest.fixture
def mock_ragas_answer_relevancy_calculate_similarity():
    import numpy

    patcher = mock.patch("ragas.metrics.answer_relevancy.calculate_similarity")
    MockRagasCalcSim = patcher.start()
    MockRagasCalcSim.return_value = numpy.array([1.0, 1.0, 1.0])
    yield MockRagasCalcSim
    patcher.stop()


@pytest.fixture
def tracer():
    return DummyTracer()


@pytest.fixture
def llmobs_env():
    return {
        "DD_API_KEY": "<default-not-a-real-key>",
        "DD_LLMOBS_ML_APP": "unnamed-ml-app",
    }


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def enqueue(self, event):
        self.events.append(event)


@pytest.fixture
def llmobs_span_writer():
    yield TestLLMObsSpanWriter(interval=1.0, timeout=1.0)


@pytest.fixture
def llmobs(
    ddtrace_global_config,
    monkeypatch,
    tracer,
    llmobs_env,
    llmobs_span_writer,
    mock_llmobs_eval_metric_writer,
    mock_llmobs_evaluator_runner,
):
    for env, val in llmobs_env.items():
        monkeypatch.setenv(env, val)
    global_config = default_global_config()
    global_config.update(dict(_llmobs_ml_app=llmobs_env.get("DD_LLMOBS_ML_APP")))
    global_config.update(ddtrace_global_config)
    # TODO: remove once rest of tests are moved off of global config tampering
    with override_global_config(global_config):
        llmobs_service.enable(_tracer=tracer)
        llmobs_service._instance._llmobs_span_writer = llmobs_span_writer
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(llmobs, llmobs_span_writer):
    return llmobs_span_writer.events
