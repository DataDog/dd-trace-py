import json
import os
import time

import mock
import pytest

from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._evaluators.sampler import EvaluatorRunnerSampler
from ddtrace.llmobs._evaluators.sampler import EvaluatorRunnerSamplingRule
from ddtrace.trace import Span
from tests.llmobs._utils import DummyEvaluator
from tests.llmobs._utils import _dummy_evaluator_eval_metric_event
from tests.utils import override_env
from tests.utils import override_global_config


DUMMY_SPAN = Span("dummy_span")


@pytest.fixture
def active_evaluator_runner(llmobs):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=llmobs)
    evaluator_runner.evaluators.append(DummyEvaluator(llmobs_service=llmobs))
    evaluator_runner.start()
    yield evaluator_runner


def test_evaluator_runner_start(mock_evaluator_logs, active_evaluator_runner):
    mock_evaluator_logs.debug.assert_has_calls([mock.call("started %r", "EvaluatorRunner")])


def test_evaluator_runner_buffer_limit(mock_evaluator_logs):
    evaluator_runner = EvaluatorRunner(interval=1, llmobs_service=mock.MagicMock())
    evaluator_runner.evaluators.append(DummyEvaluator(llmobs_service=mock.MagicMock()))
    evaluator_runner.start()
    for _ in range(1001):
        evaluator_runner.enqueue({}, DUMMY_SPAN)
    mock_evaluator_logs.warning.assert_called_with(
        "%r event buffer full (limit is %d), dropping event", "EvaluatorRunner", 1000
    )


def test_evaluator_runner_periodic_enqueues_eval_metric(mock_llmobs_eval_metric_writer, active_evaluator_runner):
    active_evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)
    active_evaluator_runner.periodic()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_evaluator_eval_metric_event(span_id="123", trace_id="1234")
    )


def test_evaluator_runner_stopped_does_not_enqueue_metric(llmobs, mock_llmobs_eval_metric_writer):
    evaluator_runner = EvaluatorRunner(interval=0.1, llmobs_service=llmobs)
    evaluator_runner.start()
    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)
    assert not evaluator_runner._buffer
    assert mock_llmobs_eval_metric_writer.enqueue.call_count == 0


def test_evaluator_runner_timed_enqueues_eval_metric(llmobs, mock_llmobs_eval_metric_writer, active_evaluator_runner):
    active_evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)

    time.sleep(0.1)

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _dummy_evaluator_eval_metric_event(span_id="123", trace_id="1234")
    )


@pytest.mark.vcr_logs
def test_evaluator_runner_multiple_evaluators(llmobs, mock_llmobs_eval_metric_writer):
    evaluator_runner = EvaluatorRunner(interval=0.01, llmobs_service=llmobs)
    evaluator_runner.evaluators += [
        DummyEvaluator(llmobs_service=llmobs, label="1"),
        DummyEvaluator(llmobs_service=llmobs, label="2"),
        DummyEvaluator(llmobs_service=llmobs, label="3"),
    ]
    evaluator_runner.start()

    evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, DUMMY_SPAN)

    time.sleep(0.1)

    calls = [call[0][0] for call in mock_llmobs_eval_metric_writer.enqueue.call_args_list]
    sorted_calls = sorted(calls, key=lambda x: x["label"])
    assert sorted_calls == [
        _dummy_evaluator_eval_metric_event(span_id="123", trace_id="1234", label="1"),
        _dummy_evaluator_eval_metric_event(span_id="123", trace_id="1234", label="2"),
        _dummy_evaluator_eval_metric_event(span_id="123", trace_id="1234", label="3"),
    ]


def test_evaluator_runner_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(__file__)))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath), "_DD_LLMOBS_EVALUATOR_INTERVAL": "5"})
    out, err, status, pid = run_python_code_in_subprocess(
        """
import os
import time
import atexit
import mock
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from tests.llmobs._utils import logs_vcr
from tests.llmobs._utils import DummyEvaluator

ctx = logs_vcr.use_cassette("tests.llmobs.test_llmobs_evaluator_runner.send_score_metric.yaml")
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
LLMObs.enable(api_key="dummy-api-key", site="datad0g.com", ml_app="unnamed-ml-app")
LLMObs._instance._evaluator_runner.evaluators.append(DummyEvaluator(llmobs_service=LLMObs))
LLMObs._instance._evaluator_runner.start()
LLMObs._instance._evaluator_runner.enqueue({"span_id": "123", "trace_id": "1234"}, None)
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


def test_evaluator_runner_unsupported_evaluator():
    with override_env({EvaluatorRunner.EVALUATORS_ENV_VAR: "unsupported"}):
        with pytest.raises(ValueError):
            EvaluatorRunner(interval=0.01, llmobs_service=mock.MagicMock())


def test_evaluator_runner_sampler_single_rule(monkeypatch):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"}]),
    )
    sampling_rules = EvaluatorRunnerSampler().rules
    assert len(sampling_rules) == 1
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[0].span_name == "dummy_span"


def test_evaluator_runner_sampler_multiple_rules(monkeypatch):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps(
            [
                {"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"},
                {"sample_rate": 0.2, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span_2"},
            ]
        ),
    )
    sampling_rules = EvaluatorRunnerSampler().rules
    assert len(sampling_rules) == 2
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[0].span_name == "dummy_span"

    assert sampling_rules[1].sample_rate == 0.2
    assert sampling_rules[1].evaluator_label == "ragas_faithfulness"
    assert sampling_rules[1].span_name == "dummy_span_2"


def test_evaluator_runner_sampler_no_rule_label_or_name(monkeypatch):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": 0.5}]),
    )
    sampling_rules = EvaluatorRunnerSampler().rules
    assert len(sampling_rules) == 1
    assert sampling_rules[0].sample_rate == 0.5
    assert sampling_rules[0].evaluator_label == EvaluatorRunnerSamplingRule.NO_RULE
    assert sampling_rules[0].span_name == EvaluatorRunnerSamplingRule.NO_RULE


def test_evaluator_sampler_invalid_json(monkeypatch, mock_evaluator_sampler_logs):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        "not a json",
    )

    with override_global_config({"_raise": True}):
        with pytest.raises(ValueError):
            EvaluatorRunnerSampler().rules

    with override_global_config({"_raise": False}):
        sampling_rules = EvaluatorRunnerSampler().rules
        assert len(sampling_rules) == 0
        mock_evaluator_sampler_logs.warning.assert_called_once_with(
            "Failed to parse evaluator sampling rules of: `not a json`", exc_info=True
        )


def test_evaluator_sampler_invalid_rule_not_a_list(monkeypatch, mock_evaluator_sampler_logs):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps({"sample_rate": 0.5, "evaluator_label": "ragas_faithfulness", "span_name": "dummy_span"}),
    )

    with override_global_config({"_raise": True}):
        with pytest.raises(ValueError):
            EvaluatorRunnerSampler().rules

    with override_global_config({"_raise": False}):
        sampling_rules = EvaluatorRunnerSampler().rules
        assert len(sampling_rules) == 0
        mock_evaluator_sampler_logs.warning.assert_called_once_with(
            "Evaluator sampling rules must be a list of dictionaries", exc_info=True
        )


def test_evaluator_sampler_invalid_rule_missing_sample_rate(monkeypatch, mock_evaluator_sampler_logs):
    monkeypatch.setenv(
        EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR,
        json.dumps([{"sample_rate": 0.1, "span_name": "dummy"}, {"span_name": "dummy2"}]),
    )

    with override_global_config({"_raise": True}):
        with pytest.raises(KeyError):
            EvaluatorRunnerSampler().rules

    with override_global_config({"_raise": False}):
        sampling_rules = EvaluatorRunnerSampler().rules
        assert len(sampling_rules) == 1
        mock_evaluator_sampler_logs.warning.assert_called_once_with(
            'No sample_rate provided for sampling rule: {"span_name": "dummy2"}', exc_info=True
        )


def test_evaluator_runner_sampler_no_rules_samples_all(monkeypatch):
    iterations = int(1e4)

    sampled = sum(EvaluatorRunnerSampler().sample("ragas_faithfulness", Span(name=str(i))) for i in range(iterations))

    deviation = abs(sampled - (iterations)) / (iterations)
    assert deviation < 0.05


def test_evaluator_sampling_rule_matches(monkeypatch):
    sample_rate = 0.5
    span_name_rule = "dummy_span"
    evaluator_label_rule = "ragas_faithfulness"

    for rule in [
        {"evaluator_label": evaluator_label_rule},
        {"evaluator_label": evaluator_label_rule, "span_name": span_name_rule},
        {"span_name": span_name_rule},
    ]:
        rule["sample_rate"] = sample_rate
        with override_env({EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR: json.dumps([rule])}):
            iterations = int(1e4 / sample_rate)
            sampled = sum(
                EvaluatorRunnerSampler().sample(evaluator_label_rule, Span(name=span_name_rule))
                for i in range(iterations)
            )

            deviation = abs(sampled - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05


def test_evaluator_sampling_does_not_match_samples_all(monkeypatch):
    sample_rate = 0.5
    span_name_rule = "dummy_span"
    evaluator_label_rule = "ragas_faithfulness"

    for rule in [
        {"evaluator_label": evaluator_label_rule},
        {"evaluator_label": evaluator_label_rule, "span_name": span_name_rule},
        {"span_name": span_name_rule},
    ]:
        rule["sample_rate"] = sample_rate
        with override_env({EvaluatorRunnerSampler.SAMPLING_RULES_ENV_VAR: json.dumps([rule])}):
            iterations = int(1e4 / sample_rate)

            label_and_span = "not a matching label", Span(name="not matching span name")

            assert EvaluatorRunnerSampler().rules[0].matches(*label_and_span) is False

            sampled = sum(EvaluatorRunnerSampler().sample(*label_and_span) for i in range(iterations))

            deviation = abs(sampled - (iterations)) / (iterations)
            assert deviation < 0.05
