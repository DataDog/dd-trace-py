import os
import time

import mock
import pytest

import ddtrace
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent


def _dummy_ragas_eval_metric_event(span_id, trace_id):
    return LLMObsEvaluationMetricEvent(
        span_id=span_id,
        trace_id=trace_id,
        score_value=1.0,
        ml_app="unnamed-ml-app",
        timestamp_ms=mock.ANY,
        metric_type="score",
        label="ragas_faithfulness",
        tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:unnamed-ml-app"],
    )


def test_evaluator_runner_start(mock_evaluator_logs, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=mock.MagicMock())
    evaluator_runner.evaluators.append(mock_ragas_evaluator)
    evaluator_runner.start()
    mock_evaluator_logs.debug.assert_has_calls([mock.call("started %r to %r", "EvaluatorRunner")])


def test_evaluator_runner_buffer_limit(mock_evaluator_logs):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=mock.MagicMock())
    for _ in range(1001):
        evaluator_runner.enqueue({})
    mock_evaluator_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "EvaluatorRunner", 1000
    )


def test_evaluator_runner_periodic_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=LLMObs)
    evaluator_runner.evaluators.append(mock_ragas_evaluator(llmobs_service=LLMObs))
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})
    evaluator_runner.periodic()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


@pytest.mark.vcr_logs
def test_evaluator_runner_timed_enqueues_eval_metric(LLMObs, mock_llmobs_eval_metric_writer, mock_ragas_evaluator):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=LLMObs)
    evaluator_runner.evaluators.append(mock_ragas_evaluator(llmobs_service=LLMObs))
    evaluator_runner.start()

    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})

    time.sleep(0.1)

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_ragas_eval_metric_event(span_id="123", trace_id="1234")
    )


def test_evaluator_runner_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "DD_API_KEY": "foobar.baz",
            "DD_SITE": "datadoghq.com",
            "PYTHONPATH": ":".join(pypath),
            "DD_LLMOBS_ML_APP": "unnamed-ml-app",
            "_DD_LLMOBS_WRITER_INTERVAL": "0.01",
        }
    )
    out, err, status, pid = run_python_code_in_subprocess(
        """
import os
import time
import atexit
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from tests.llmobs._utils import logs_vcr
from tests.llmobs._utils import DummyEvaluator

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_evaluator_runner.send_score_metric.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
LLMObs.enable()
evaluator_runner = EvaluatorRunner(
    interval=0.01, llmobs_service=LLMObs
)
evaluator_runner.evaluators.append(DummyEvaluator(llmobs_service=LLMObs))
evaluator_runner.start()
evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"})
evaluator_runner.periodic()
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""
