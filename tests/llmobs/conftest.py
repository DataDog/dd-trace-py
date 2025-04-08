from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import os
import threading
import time

import mock
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from tests.llmobs._utils import TestLLMObsSpanWriter
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
def mock_writer_logs():
    with mock.patch("ddtrace.llmobs._writer.logger") as m:
        yield m


@pytest.fixture
def mock_evaluator_logs():
    with mock.patch("ddtrace.llmobs._evaluators.runner.logger") as m:
        yield m
        m.reset_mock()


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


@pytest.fixture
def llmobs_span_writer(_llmobs_backend):
    url, _ = _llmobs_backend
    site = "datad0g.com"
    api_key = "<test-key>"
    yield TestLLMObsSpanWriter(site, api_key, 1.0, 1.0, is_agentless=True, _agentless_url=url)


class LLMObsServer(BaseHTTPRequestHandler):
    """A mock server for the LLMObs backend used to capture the requests made by the client.

    Python's HTTPRequestHandler is a bit weird and uses a class rather than an instance
    for running an HTTP server so the requests are stored in a class variable and reset in the pytest fixture.
    """

    requests = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def do_POST(self) -> None:
        content_length = int(self.headers["Content-Length"])
        body = self.rfile.read(content_length).decode("utf-8")
        self.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


@pytest.fixture
def _llmobs_backend():
    LLMObsServer.requests = []
    # Create and start the HTTP server
    server = HTTPServer(("localhost", 0), LLMObsServer)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    # Provide the server details to the test
    server_address = f"http://{server.server_address[0]}:{server.server_address[1]}"

    yield server_address, LLMObsServer.requests

    # Stop the server after the test
    server.shutdown()
    server.server_close()


@pytest.fixture
def llmobs_backend(_llmobs_backend):
    _, reqs = _llmobs_backend

    class _LLMObsBackend:
        def wait_for_num_events(self, num, attempts=1000):
            for _ in range(attempts):
                if len(reqs) == num:
                    return [json.loads(r["body"]) for r in reqs]
                # time.sleep will yield the GIL so the server can process the request
                time.sleep(0.001)
            else:
                raise TimeoutError(f"Expected {num} events, got {len(reqs)}")

    return _LLMObsBackend()


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
        llmobs_service._instance._llmobs_span_writer.start()
        yield llmobs_service
    llmobs_service.disable()


@pytest.fixture
def llmobs_events(llmobs, llmobs_span_writer):
    return llmobs_span_writer.events


@pytest.fixture
def agent():
    with mock.patch("ddtrace.internal.agent.info", return_value={"endpoints": ["/evp_proxy/v2/"]}):
        yield


@pytest.fixture
def agent_missing_proxy():
    with mock.patch("ddtrace.internal.agent.info", return_value={"endpoints": []}):
        yield


@pytest.fixture
def no_agent_info():
    with mock.patch("ddtrace.internal.agent.info", return_value=None):
        yield


@pytest.fixture
def no_agent():
    with mock.patch("ddtrace.internal.agent.info", side_effect=Exception):
        yield
