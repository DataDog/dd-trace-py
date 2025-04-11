import os

import mock
import pytest

from ddtrace.llmobs._evaluators.ragas.answer_relevancy import RagasAnswerRelevancyEvaluator
from ddtrace.llmobs._evaluators.ragas.context_precision import RagasContextPrecisionEvaluator
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.trace import Span
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_ragas_answer_relevancy_spans
from tests.llmobs._utils import _expected_ragas_context_precision_spans
from tests.llmobs._utils import _expected_ragas_faithfulness_spans
from tests.llmobs._utils import _llm_span_with_expected_ragas_inputs_in_messages
from tests.llmobs._utils import _llm_span_with_expected_ragas_inputs_in_prompt
from tests.llmobs._utils import default_ragas_inputs
from tests.llmobs._utils import logs_vcr


pytest.importorskip("ragas", reason="Tests require ragas to be available on user env")

ragas_answer_relevancy_cassette = logs_vcr.use_cassette(
    "tests.llmobs.test_llmobs_ragas_evaluators.answer_relevancy_inference.yaml"
)

ragas_context_precision_single_context_cassette = logs_vcr.use_cassette(
    "tests.llmobs.test_llmobs_ragas_evaluators.test_ragas_context_precision_single_context.yaml"
)
ragas_context_precision_multiple_context_cassette = logs_vcr.use_cassette(
    "tests.llmobs.test_llmobs_ragas_evaluators.test_ragas_context_precision_multiple_context.yaml"
)


@pytest.fixture
def reset_ragas_context_precision_llm():
    import ragas

    previous_llm = ragas.metrics.context_precision.llm
    yield
    ragas.metrics.context_precision.llm = previous_llm


def _llm_span_without_io():
    return _expected_llmobs_llm_span_event(Span("dummy"))


def test_ragas_evaluator_init(ragas, llmobs):
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    assert rf_evaluator.llmobs_service == llmobs
    assert rf_evaluator.ragas_faithfulness_instance == ragas.metrics.faithfulness
    assert rf_evaluator.ragas_faithfulness_instance.llm == ragas.llms.llm_factory()


def test_ragas_faithfulness_throws_if_dependencies_not_present(llmobs, mock_ragas_dependencies_not_present, ragas):
    with pytest.raises(NotImplementedError, match="Failed to load dependencies for `ragas_faithfulness` evaluator"):
        RagasFaithfulnessEvaluator(llmobs)


def test_ragas_faithfulness_returns_none_if_inputs_extraction_fails(ragas, mock_llmobs_submit_evaluation, llmobs):
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    failure_msg, _ = rf_evaluator.evaluate(_llm_span_without_io())
    assert failure_msg == "fail_extract_faithfulness_inputs"
    assert rf_evaluator.llmobs_service.submit_evaluation.call_count == 0


def test_ragas_faithfulness_has_modified_faithfulness_instance(
    ragas, mock_llmobs_submit_evaluation, reset_ragas_faithfulness_llm, llmobs
):
    """Faithfulness instance used in ragas evaluator should match the global ragas faithfulness instance"""
    from ragas.llms import BaseRagasLLM
    from ragas.metrics import faithfulness

    class FirstDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self) -> str:
            return "dummy llm"

        def agenerate_text(self) -> str:
            return "dummy llm"

    faithfulness.llm = FirstDummyLLM()

    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)

    assert rf_evaluator.ragas_faithfulness_instance.llm.generate_text() == "dummy llm"

    class SecondDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self, statements) -> str:
            raise ValueError("dummy_llm")

        def agenerate_text(self, statements) -> str:
            raise ValueError("dummy_llm")

    faithfulness.llm = SecondDummyLLM()

    with pytest.raises(ValueError, match="dummy_llm"):
        rf_evaluator.evaluate(_llm_span_with_expected_ragas_inputs_in_prompt())


@pytest.mark.vcr_logs
def test_ragas_faithfulness_submits_evaluation(ragas, llmobs, mock_llmobs_submit_evaluation):
    """Test that evaluation is submitted for a valid llm span where question is in the prompt variables"""
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_prompt()
    rf_evaluator.run_and_submit_evaluation(llm_span)
    rf_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=1.0,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                    "_dd.faithfulness_disagreements": mock.ANY,
                    "_dd.evaluation_kind": "faithfulness",
                },
            )
        ]
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_submits_evaluation_on_span_with_question_in_messages(
    ragas, llmobs, mock_llmobs_submit_evaluation
):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_messages()
    rf_evaluator.run_and_submit_evaluation(llm_span)
    rf_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=1.0,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                    "_dd.faithfulness_disagreements": mock.ANY,
                    "_dd.evaluation_kind": "faithfulness",
                },
            )
        ]
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_submits_evaluation_on_span_with_custom_keys(ragas, llmobs, mock_llmobs_submit_evaluation):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    llm_span = _expected_llmobs_llm_span_event(
        Span("dummy"),
        prompt={
            "variables": {
                "user_input": "Is france part of europe?",
                "context_1": "hello, ",
                "context_2": "france is ",
                "context_3": "part of europe",
            },
            "_dd_context_variable_keys": ["context_1", "context_2", "context_3"],
            "_dd_query_variable_keys": ["user_input"],
        },
        output_messages=[{"content": "France is indeed part of europe"}],
    )
    rf_evaluator.run_and_submit_evaluation(llm_span)
    rf_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=1.0,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                    "_dd.faithfulness_disagreements": mock.ANY,
                    "_dd.evaluation_kind": "faithfulness",
                },
            )
        ]
    )


@pytest.mark.vcr_logs
def test_ragas_faithfulness_emits_traces(ragas, llmobs, llmobs_events):
    rf_evaluator = RagasFaithfulnessEvaluator(llmobs)
    rf_evaluator.evaluate(_llm_span_with_expected_ragas_inputs_in_prompt())
    ragas_spans = [event for event in llmobs_events if event["name"].startswith("dd-ragas.")]
    ragas_spans = sorted(ragas_spans, key=lambda d: d["start_ns"])
    assert len(ragas_spans) == 7
    # check name, io, span kinds match
    assert ragas_spans == _expected_ragas_faithfulness_spans()

    # verify the trace structure
    root_span = ragas_spans[0]
    root_span_id = root_span["span_id"]
    assert root_span["parent_id"] == "undefined"
    assert root_span["meta"] is not None
    assert root_span["meta"]["metadata"] is not None
    assert isinstance(root_span["meta"]["metadata"]["faithfulness_list"], list)
    assert isinstance(root_span["meta"]["metadata"]["statements"], list)
    root_span_trace_id = root_span["trace_id"]
    for child_span in ragas_spans[1:]:
        assert child_span["trace_id"] == root_span_trace_id

    assert ragas_spans[1]["parent_id"] == root_span_id  # input extraction (task)
    assert ragas_spans[2]["parent_id"] == root_span_id  # create statements (workflow)
    assert ragas_spans[4]["parent_id"] == root_span_id  # create verdicts (workflow)
    assert ragas_spans[6]["parent_id"] == root_span_id  # create score (task)
    assert ragas_spans[3]["parent_id"] == ragas_spans[2]["span_id"]  # create statements prompt (task)
    assert ragas_spans[5]["parent_id"] == ragas_spans[4]["span_id"]  # create verdicts prompt (task)


def test_llmobs_with_faithfulness_emits_traces_and_evals_on_exit(mock_writer_logs, run_python_code_in_subprocess):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(__file__)))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": ":".join(pypath),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "dummy-openai-api-key"),
            "_DD_LLMOBS_EVALUATOR_INTERVAL": "5",
            EvaluatorRunner.EVALUATORS_ENV_VAR: "ragas_faithfulness",
            "DD_TRACE_ENABLED": "0",
        }
    )
    out, err, status, pid = run_python_code_in_subprocess(
        """
import os
import time
import atexit
import mock
from ddtrace.llmobs import LLMObs
from ddtrace.internal.utils.http import Response
from tests.llmobs._utils import _llm_span_with_expected_ragas_inputs_in_messages
from tests.llmobs._utils import logs_vcr

ctx = logs_vcr.use_cassette(
    "tests.llmobs.test_llmobs_ragas_evaluators.emits_traces_and_evaluations_on_exit.yaml"
)
ctx.__enter__()
atexit.register(lambda: ctx.__exit__())
with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload", return_value=Response(status=200, body="{}")):
    LLMObs.enable(api_key="dummy-api-key", site="datad0g.com", ml_app="unnamed-ml-app", agentless_enabled=True)
    LLMObs._instance._evaluator_runner.enqueue(_llm_span_with_expected_ragas_inputs_in_messages(), None)
    """,
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


def test_ragas_context_precision_init(ragas, llmobs):
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    assert rcp_evaluator.llmobs_service == llmobs
    assert rcp_evaluator.ragas_context_precision_instance == ragas.metrics.context_precision
    assert rcp_evaluator.ragas_context_precision_instance.llm == ragas.llms.llm_factory()


def test_ragas_context_precision_throws_if_dependencies_not_present(llmobs, mock_ragas_dependencies_not_present, ragas):
    with pytest.raises(
        NotImplementedError, match="Failed to load dependencies for `ragas_context_precision` evaluator"
    ):
        RagasContextPrecisionEvaluator(llmobs)


def test_ragas_context_precision_returns_none_if_inputs_extraction_fails(ragas, mock_llmobs_submit_evaluation, llmobs):
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    failure_msg, _ = rcp_evaluator.evaluate(_llm_span_without_io())
    assert failure_msg == "fail_extract_context_precision_inputs"
    assert rcp_evaluator.llmobs_service.submit_evaluation.call_count == 0


def test_ragas_context_precision_has_modified_context_precision_instance(
    ragas, mock_llmobs_submit_evaluation, reset_ragas_context_precision_llm, llmobs
):
    """Context precision instance used in ragas evaluator should match the global ragas context precision instance"""
    from ragas.llms import BaseRagasLLM
    from ragas.metrics import context_precision

    class FirstDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self) -> str:
            return "dummy llm"

        def agenerate_text(self) -> str:
            return "dummy llm"

    context_precision.llm = FirstDummyLLM()

    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)

    assert rcp_evaluator.ragas_context_precision_instance.llm.generate_text() == "dummy llm"

    class SecondDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self) -> str:
            return "second dummy llm"

        def agenerate_text(self) -> str:
            return "second dummy llm"

    context_precision.llm = SecondDummyLLM()

    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)

    assert rcp_evaluator.ragas_context_precision_instance.llm.generate_text() == "second dummy llm"


def test_ragas_context_precision_submits_evaluation(ragas, llmobs, mock_llmobs_submit_evaluation):
    """Test that evaluation is submitted for a valid llm span where question is in the prompt variables"""
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_prompt()
    with ragas_context_precision_single_context_cassette:
        rcp_evaluator.run_and_submit_evaluation(llm_span)
    rcp_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasContextPrecisionEvaluator.LABEL,
                metric_type=RagasContextPrecisionEvaluator.METRIC_TYPE,
                value=1.0,
                metadata={
                    "_dd.evaluation_kind": "context_precision",
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_context_precision_submits_evaluation_on_span_with_question_in_messages(
    ragas, llmobs, mock_llmobs_submit_evaluation
):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_messages()
    with ragas_context_precision_single_context_cassette:
        rcp_evaluator.run_and_submit_evaluation(llm_span)
    rcp_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasContextPrecisionEvaluator.LABEL,
                metric_type=RagasContextPrecisionEvaluator.METRIC_TYPE,
                value=1.0,
                metadata={
                    "_dd.evaluation_kind": "context_precision",
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_context_precision_submits_evaluation_on_span_with_custom_keys(
    ragas, llmobs, mock_llmobs_submit_evaluation
):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    llm_span = _expected_llmobs_llm_span_event(
        Span("dummy"),
        prompt={
            "variables": {
                "user_input": default_ragas_inputs["question"],
                "context_2": default_ragas_inputs["context"],
                "context_3": default_ragas_inputs["context"],
            },
            "_dd_context_variable_keys": ["context_2", "context_3"],
            "_dd_query_variable_keys": ["user_input"],
        },
        output_messages=[{"content": default_ragas_inputs["answer"]}],
    )
    with ragas_context_precision_multiple_context_cassette:
        rcp_evaluator.run_and_submit_evaluation(llm_span)
    rcp_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasContextPrecisionEvaluator.LABEL,
                metric_type=RagasContextPrecisionEvaluator.METRIC_TYPE,
                value=0.5,
                metadata={
                    "_dd.evaluation_kind": "context_precision",
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_context_precision_emits_traces(ragas, llmobs, llmobs_events):
    rcp_evaluator = RagasContextPrecisionEvaluator(llmobs)
    with ragas_context_precision_single_context_cassette:
        rcp_evaluator.evaluate(_llm_span_with_expected_ragas_inputs_in_prompt())
    ragas_spans = [event for event in llmobs_events if event["name"].startswith("dd-ragas.")]
    ragas_spans = sorted(ragas_spans, key=lambda d: d["start_ns"])
    assert len(ragas_spans) == 2
    assert ragas_spans == _expected_ragas_context_precision_spans()

    # verify the trace structure
    root_span = ragas_spans[0]
    root_span_id = root_span["span_id"]
    assert root_span["parent_id"] == "undefined"
    assert root_span["meta"] is not None

    root_span_trace_id = root_span["trace_id"]
    for child_span in ragas_spans[1:]:
        assert child_span["trace_id"] == root_span_trace_id
        assert child_span["parent_id"] == root_span_id


def test_ragas_answer_relevancy_init(ragas, llmobs):
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    assert rar_evaluator.llmobs_service == llmobs
    assert rar_evaluator.ragas_answer_relevancy_instance == ragas.metrics.answer_relevancy
    assert rar_evaluator.ragas_answer_relevancy_instance.llm == ragas.llms.llm_factory()
    assert (
        rar_evaluator.ragas_answer_relevancy_instance.embeddings.embeddings
        == ragas.embeddings.embedding_factory().embeddings
    )
    assert (
        rar_evaluator.ragas_answer_relevancy_instance.embeddings.run_config
        == ragas.embeddings.embedding_factory().run_config
    )


def test_ragas_answer_relevancy_throws_if_dependencies_not_present(llmobs, mock_ragas_dependencies_not_present, ragas):
    with pytest.raises(NotImplementedError, match="Failed to load dependencies for `ragas_answer_relevancy` evaluator"):
        RagasAnswerRelevancyEvaluator(llmobs)


def test_ragas_answer_relevancy_returns_none_if_inputs_extraction_fails(ragas, mock_llmobs_submit_evaluation, llmobs):
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    failure_msg, _ = rar_evaluator.evaluate(_llm_span_without_io())
    assert failure_msg == "fail_extract_answer_relevancy_inputs"
    assert rar_evaluator.llmobs_service.submit_evaluation.call_count == 0


def test_ragas_answer_relevancy_has_modified_answer_relevancy_instance(
    ragas, mock_llmobs_submit_evaluation, reset_ragas_answer_relevancy_llm, llmobs
):
    """Answer relevancy instance used in ragas evaluator should match the global ragas context precision instance"""
    from ragas.llms import BaseRagasLLM
    from ragas.metrics import answer_relevancy

    class FirstDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self) -> str:
            return "dummy llm"

        def agenerate_text(self) -> str:
            return "dummy llm"

    answer_relevancy.llm = FirstDummyLLM()

    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)

    assert rar_evaluator.ragas_answer_relevancy_instance.llm.generate_text() == "dummy llm"

    class SecondDummyLLM(BaseRagasLLM):
        def __init__(self):
            super().__init__()

        def generate_text(self) -> str:
            return "second dummy llm"

        def agenerate_text(self) -> str:
            return "second dummy llm"

    answer_relevancy.llm = SecondDummyLLM()

    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)

    assert rar_evaluator.ragas_answer_relevancy_instance.llm.generate_text() == "second dummy llm"


def test_ragas_answer_relevancy_submits_evaluation(
    ragas, llmobs, mock_llmobs_submit_evaluation, mock_ragas_answer_relevancy_calculate_similarity
):
    """Test that evaluation is submitted for a valid llm span where question is in the prompt variables"""
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_prompt()
    with ragas_answer_relevancy_cassette:
        rar_evaluator.run_and_submit_evaluation(llm_span)
    rar_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasAnswerRelevancyEvaluator.LABEL,
                metric_type=RagasAnswerRelevancyEvaluator.METRIC_TYPE,
                value=mock.ANY,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_answer_relevancy_submits_evaluation_on_span_with_question_in_messages(
    ragas, llmobs, mock_llmobs_submit_evaluation, mock_ragas_answer_relevancy_calculate_similarity
):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    llm_span = _llm_span_with_expected_ragas_inputs_in_messages()
    with ragas_answer_relevancy_cassette:
        rar_evaluator.run_and_submit_evaluation(llm_span)
    rar_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasAnswerRelevancyEvaluator.LABEL,
                metric_type=RagasAnswerRelevancyEvaluator.METRIC_TYPE,
                value=mock.ANY,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_answer_relevancy_submits_evaluation_on_span_with_custom_keys(
    ragas, llmobs, mock_llmobs_submit_evaluation, mock_ragas_answer_relevancy_calculate_similarity
):
    """Test that evaluation is submitted for a valid llm span where the last message content is the question"""
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    llm_span = _expected_llmobs_llm_span_event(
        Span("dummy"),
        prompt={
            "variables": {
                "user_input": "Is france part of europe?",
                "context_2": "irrelevant",
                "context_3": "France is part of europe",
            },
            "_dd_context_variable_keys": ["context_2", "context_3"],
            "_dd_query_variable_keys": ["user_input"],
        },
        output_messages=[{"content": "France is indeed part of europe"}],
    )
    with ragas_answer_relevancy_cassette:
        rar_evaluator.run_and_submit_evaluation(llm_span)
    rar_evaluator.llmobs_service.submit_evaluation.assert_has_calls(
        [
            mock.call(
                span_context={
                    "span_id": llm_span.get("span_id"),
                    "trace_id": llm_span.get("trace_id"),
                },
                label=RagasAnswerRelevancyEvaluator.LABEL,
                metric_type=RagasAnswerRelevancyEvaluator.METRIC_TYPE,
                value=mock.ANY,
                metadata={
                    "_dd.evaluation_span": {"span_id": mock.ANY, "trace_id": mock.ANY},
                },
            )
        ]
    )


def test_ragas_answer_relevancy_emits_traces(
    ragas, llmobs, llmobs_events, mock_ragas_answer_relevancy_calculate_similarity
):
    rar_evaluator = RagasAnswerRelevancyEvaluator(llmobs)
    with ragas_answer_relevancy_cassette:
        rar_evaluator.evaluate(_llm_span_with_expected_ragas_inputs_in_prompt())

    ragas_spans = [event for event in llmobs_events if event["name"].startswith("dd-ragas.")]
    ragas_spans = sorted(ragas_spans, key=lambda d: d["start_ns"])

    assert len(ragas_spans) == 3
    # check name, io, span kinds match
    assert ragas_spans == _expected_ragas_answer_relevancy_spans()

    # verify the trace structure
    root_span = ragas_spans[0]
    root_span_id = root_span["span_id"]
    assert root_span["parent_id"] == "undefined"
    assert root_span["meta"] is not None

    root_span_trace_id = root_span["trace_id"]
    for child_span in ragas_spans[1:]:
        assert child_span["trace_id"] == root_span_trace_id
        assert child_span["parent_id"] == root_span_id
