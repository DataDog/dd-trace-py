"""Tests for Pydantic AI evaluator integration with LLMObs experiments."""

import asyncio
from dataclasses import dataclass
import os

import pytest


pydantic_evals = pytest.importorskip("pydantic_evals")

from pydantic_evals.evaluators import Evaluator  # noqa: E402
from pydantic_evals.evaluators import EvaluatorContext  # noqa: E402
from pydantic_evals.evaluators import ReportEvaluator  # noqa: E402
from pydantic_evals.evaluators import ReportEvaluatorContext  # noqa: E402
from pydantic_evals.evaluators.evaluator import EvaluationReason  # noqa: E402
from pydantic_evals.reporting import ReportAnalysis  # noqa: E402
from pydantic_evals.reporting import ScalarResult  # noqa: E402
from pydantic_evals.reporting import TableResult  # noqa: E402

from ddtrace.llmobs._experiment import Dataset  # noqa: E402
from ddtrace.llmobs._experiment import _ExperimentRunInfo  # noqa: E402
from ddtrace.llmobs._experiment import _is_pydantic_evaluator  # noqa: E402
from ddtrace.llmobs._experiment import _is_pydantic_report_evaluator  # noqa: E402


def _make_simple_pydantic_evaluator():
    """Build a minimal pydantic_evals Evaluator for tests (only when pydantic_evals is available)."""

    @dataclass
    class SimplePydanticEvaluator(Evaluator):
        """Minimal pydantic_evals evaluator: True when output equals expected_output, else False."""

        evaluation_name: str = "simple_pydantic_eval"

        def evaluate(self, ctx: EvaluatorContext) -> bool:
            if ctx.expected_output is None:
                return False
            return ctx.output == ctx.expected_output

        async def evaluate_async(self, ctx: EvaluatorContext) -> bool:
            if ctx.expected_output is None:
                return False
            return ctx.output == ctx.expected_output

    return SimplePydanticEvaluator()


def _make_score_pydantic_evaluator(score_when_pass=0.85, score_when_fail=0.3):
    """Build a pydantic_evals Evaluator that returns a numeric score (EvaluationReason with float value)."""

    @dataclass
    class ScorePydanticEvaluator(Evaluator):
        """Custom evaluator that returns a score and reasoning."""

        evaluation_name: str = "custom_score_eval"

        def evaluate(self, ctx: EvaluatorContext) -> EvaluationReason:
            passed = ctx.expected_output is not None and ctx.output == ctx.expected_output
            s = score_when_pass if passed else score_when_fail
            return EvaluationReason(value=s, reason="Match" if passed else "Mismatch")

    return ScorePydanticEvaluator()


class TestPydanticEvaluatorDetection:
    """Test that _is_pydantic_evaluator correctly identifies pydantic_evals evaluators."""

    def test_simple_evaluator_is_pydantic_evaluator(self):
        evaluator = _make_simple_pydantic_evaluator()
        assert _is_pydantic_evaluator(evaluator) is True

    def test_function_is_not_pydantic_evaluator(self):
        def fn(a, b, c):
            return 1

        assert _is_pydantic_evaluator(fn) is False


class TestPydanticEvaluatorEvaluate:
    """Test that a pydantic evaluator runs successfully (evaluate returns bool)."""

    def test_evaluate_pass(self):
        from pydantic_evals.evaluators import EvaluatorContext

        evaluator = _make_simple_pydantic_evaluator()
        ctx = EvaluatorContext(
            name="",
            inputs={"value": "test"},
            expected_output="Paris",
            output="Paris",
            duration=0.0,
            metadata=None,
            _span_tree=None,
            attributes={},
            metrics={},
        )
        result = evaluator.evaluate(ctx)
        assert result is True

    def test_evaluate_fail(self):
        from pydantic_evals.evaluators import EvaluatorContext

        evaluator = _make_simple_pydantic_evaluator()
        ctx = EvaluatorContext(
            name="",
            inputs={"value": "test"},
            expected_output="Paris",
            output="London",
            duration=0.0,
            metadata=None,
            _span_tree=None,
            attributes={},
            metrics={},
        )
        result = evaluator.evaluate(ctx)
        assert result is False


class TestPydanticEvaluatorInExperiment:
    """Test that a pydantic evaluator runs successfully inside an experiment run."""

    def test_experiment_run_with_pydantic_evaluator(self, llmobs):
        """Run an experiment with a pydantic evaluator and assert it completes with correct results."""

        def dummytask(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "test",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        pydantic_evaluator = _make_simple_pydantic_evaluator()
        exp = llmobs.experiment(
            "test_experiment",
            dummytask,
            dataset,
            [pydantic_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        assert "simple_pydantic_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_pydantic_eval"]
        assert result["error"] is None
        assert result["value"] is True
        assert result["assessment"] == "pass"

    def test_experiment_run_with_pydantic_evaluator_fail(self, llmobs):
        """Pydantic evaluator returns False when output != expected_output."""

        def task(input_data, config):
            return "London"

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "Paris",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        pydantic_evaluator = _make_simple_pydantic_evaluator()
        exp = llmobs.experiment(
            "test_experiment",
            task,
            dataset,
            [pydantic_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        assert "simple_pydantic_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_pydantic_eval"]
        assert result["value"] is False
        assert result["assessment"] == "fail"

    @pytest.mark.asyncio
    async def test_async_experiment_run_with_pydantic_evaluator(self, llmobs):
        """Run an async experiment with a pydantic evaluator and assert it completes with correct results."""

        async def async_dummytask(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "test",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        pydantic_evaluator = _make_simple_pydantic_evaluator()
        exp = llmobs.async_experiment(
            "test_async_experiment",
            async_dummytask,
            dataset,
            [pydantic_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        assert "simple_pydantic_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["simple_pydantic_eval"]
        assert result["error"] is None
        assert result["value"] is True
        assert result["assessment"] == "pass"


def _make_mock_llm_judge():
    """Build a pydantic_evals LLMJudge that returns a fixed result without calling OpenAI."""
    from pydantic_evals.evaluators import LLMJudge

    class MockLLMJudge(LLMJudge):
        """LLMJudge subclass that returns a fixed score/pass without calling OpenAI."""

        evaluation_name: str = "mock_llm_judge"

        async def evaluate(self, ctx: EvaluatorContext):
            return EvaluationReason(value=True, reason="mocked judge pass")

    return MockLLMJudge(rubric="Response is helpful and accurate.")


class TestPydanticCustomScoreEvaluator:
    """Test custom pydantic evaluators that return a score (float via EvaluationReason)."""

    def test_experiment_run_with_custom_score_evaluator_pass(self, llmobs):
        """Custom score evaluator returns score and reasoning when output matches expected."""

        def dummytask(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "test",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        score_evaluator = _make_score_pydantic_evaluator(score_when_pass=0.9, score_when_fail=0.2)
        exp = llmobs.experiment(
            "test_experiment",
            dummytask,
            dataset,
            [score_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        assert "custom_score_eval" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["custom_score_eval"]
        assert result["error"] is None
        assert result["value"] == 0.9
        assert result["reasoning"] == "Match"

    def test_experiment_run_with_custom_score_evaluator_fail(self, llmobs):
        """Custom score evaluator returns lower score and reasoning when output does not match."""

        def task(input_data, config):
            return "wrong"

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"value": "test"},
                    "expected_output": "expected",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        score_evaluator = _make_score_pydantic_evaluator(score_when_pass=0.9, score_when_fail=0.2)
        exp = llmobs.experiment(
            "test_experiment",
            task,
            dataset,
            [score_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))

        assert len(eval_results) == 1
        result = eval_results[0]["evaluations"]["custom_score_eval"]
        assert result["value"] == 0.2
        assert result["reasoning"] == "Mismatch"


class TestPydanticLLMJudge:
    """Test pydantic_evals LLMJudge integration with experiments (mocked to avoid OpenAI calls)."""

    def test_experiment_run_with_llm_judge_mocked(self, llmobs):
        """LLMJudge (mocked) runs in an experiment and returns pass/reasoning without calling OpenAI."""

        async def async_dummytask(input_data, config):
            return "The capital of France is Paris."

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"question": "Capital of France?"},
                    "expected_output": "Paris",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        mock_judge = _make_mock_llm_judge()
        exp = llmobs.async_experiment(
            "test_llm_judge_experiment",
            async_dummytask,
            dataset,
            [mock_judge],
        )

        async def run():
            run_info = _ExperimentRunInfo(0)
            task_results = await exp._run_task(1, run=run_info, raise_errors=False)
            return await exp._run_evaluators(task_results, raise_errors=False)

        eval_results = asyncio.run(run())

        assert len(eval_results) == 1
        evaluations = eval_results[0]["evaluations"]
        assert len(evaluations) == 1
        label = next(iter(evaluations))
        result = evaluations[label]
        assert result["error"] is None
        assert result["value"] is True
        assert result["reasoning"] == "mocked judge pass"

    @pytest.mark.asyncio
    async def test_async_experiment_run_with_llm_judge_mocked(self, llmobs):
        """Async experiment with mocked LLMJudge completes successfully."""

        async def async_task(input_data, config):
            return "Paris"

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"q": "Capital of France?"},
                    "expected_output": "Paris",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        mock_judge = _make_mock_llm_judge()
        exp = llmobs.async_experiment(
            "test_async_llm_judge",
            async_task,
            dataset,
            [mock_judge],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        evaluations = eval_results[0]["evaluations"]
        assert len(evaluations) == 1
        result = next(iter(evaluations.values()))
        assert result["error"] is None
        assert result["value"] is True
        assert result["reasoning"] == "mocked judge pass"

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY required for real LLMJudge with OpenAI",
    )
    @pytest.mark.asyncio
    async def test_async_experiment_run_with_llm_judge_openai(self, llmobs):
        """Async experiment with real pydantic_evals LLMJudge using OpenAI (requires OPENAI_API_KEY)."""
        from pydantic_evals.evaluators import LLMJudge

        async def async_task(input_data, config):
            return "The capital of France is Paris."

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {
                    "record_id": "rec_1",
                    "input_data": {"question": "What is the capital of France?"},
                    "expected_output": "Paris",
                    "metadata": {},
                }
            ],
            description="Test dataset",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        # Use a small OpenAI model for speed; rubric asks for factual correctness
        judge = LLMJudge(
            rubric="The response must correctly state the capital of France.",
            model="openai:gpt-4o-mini",
            assertion=False,
            score={},
        )
        exp = llmobs.async_experiment(
            "test_llm_judge_openai",
            async_task,
            dataset,
            [judge],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        evaluations = eval_results[0]["evaluations"]
        assert len(evaluations) >= 1
        for result in evaluations.values():
            assert result.get("error") is None


def _dataset_for_report_summary_tests():
    return Dataset(
        name="test_dataset",
        project={"name": "test_project", "_id": "proj_123"},
        dataset_id="ds_123",
        records=[
            {
                "record_id": "rec_1",
                "input_data": {"value": "test"},
                "expected_output": "test",
                "metadata": {},
            }
        ],
        description="Test dataset",
        latest_version=1,
        version=1,
        _dne_client=None,
    )


def _make_scalar_report_evaluator(return_value=2.71):
    """ReportEvaluator whose return annotation is exactly ScalarResult."""

    @dataclass
    class ScalarReportEvaluator(ReportEvaluator):
        def evaluate(self, ctx: ReportEvaluatorContext) -> ScalarResult:
            return ScalarResult(title="summary_metric", value=return_value)

        async def evaluate_async(self, ctx: ReportEvaluatorContext) -> ScalarResult:
            return ScalarResult(title="summary_metric", value=return_value)

    return ScalarReportEvaluator()


def _make_table_report_evaluator():
    """ReportEvaluator whose return annotation is TableResult (does not successfully run as a summary evaluator)."""

    @dataclass
    class TableReportEvaluator(ReportEvaluator):
        def evaluate(self, ctx: ReportEvaluatorContext) -> TableResult:
            return TableResult(title="tbl", columns=["c"], rows=[[1]])

    return TableReportEvaluator()


def _make_report_evaluator_with_analysis_union_annotation():
    """ReportEvaluator annotated with full ReportAnalysis union."""

    @dataclass
    class WideAnnotationReportEvaluator(ReportEvaluator):
        def evaluate(self, ctx: ReportEvaluatorContext) -> ReportAnalysis:
            return ScalarResult(title="t", value=1.0)

    return WideAnnotationReportEvaluator()


class TestPydanticReportSummaryEvaluatorDetection:
    """Tests for accepting only pydantic ReportEvaluators that return ScalarResult."""

    def test_scalar_return_annotation_is_accepted(self):
        ev = _make_scalar_report_evaluator()
        assert _is_pydantic_report_evaluator(ev) is True

    def test_pydantic_evaluator_not_pydantic_report_evaluator_is_rejected(self):
        ev = _make_simple_pydantic_evaluator()
        assert _is_pydantic_report_evaluator(ev) is False


class TestPydanticReportSummaryEvaluatorInExperiment:
    """Sync/async experiments: scalar ReportEvaluator summary works; other report types are rejected."""

    def test_sync_experiment_scalar_report_summary_runs(self, llmobs):
        def task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_scalar_report_evaluator(return_value=0.99)
        summary_name = summary_eval.get_serialization_name()

        exp = llmobs.experiment(
            "test_exp_scalar_report_summary",
            task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
        summary_results = asyncio.run(
            exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
        )

        assert len(summary_results) == 1
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is None
        assert summary_entry["value"] == 0.99

    def test_sync_experiment_rejects_table_report_summary(self, llmobs):
        def task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_table_report_evaluator()
        summary_name = summary_eval.get_serialization_name()

        exp = llmobs.experiment(
            "test_exp_table_report_summary",
            task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
        summary_results = asyncio.run(
            exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
        )
        summary_name = summary_eval.get_serialization_name()
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is not None
        assert (
            summary_entry["error"]["message"]
            == "Pydantic report evaluator returned a non-scalar result; only a scalar result is allowed"
        )

    def test_sync_experiment_rejects_report_analysis_union_annotation(self, llmobs):
        def task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_report_evaluator_with_analysis_union_annotation()
        summary_name = summary_eval.get_serialization_name()
        exp = llmobs.experiment(
            "test_exp_wide_report_summary",
            task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )
        run_info = _ExperimentRunInfo(0)
        task_results = asyncio.run(exp._experiment._run_task(1, run=run_info, raise_errors=False))
        eval_results = asyncio.run(exp._experiment._run_evaluators(task_results, raise_errors=False))
        summary_results = asyncio.run(
            exp._experiment._run_summary_evaluators(task_results, eval_results, raise_errors=False)
        )
        assert len(summary_results) == 1
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is None
        assert summary_entry["value"] == 1.0

    @pytest.mark.asyncio
    async def test_async_experiment_scalar_report_summary_runs(self, llmobs):
        async def async_task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_scalar_report_evaluator(return_value=0.42)
        summary_name = summary_eval.get_serialization_name()

        exp = llmobs.async_experiment(
            "test_async_exp_scalar_report_summary",
            async_task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)
        summary_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)

        assert len(summary_results) == 1
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is None
        assert summary_entry["value"] == 0.42

    async def test_async_experiment_rejects_table_report_summary(self, llmobs):
        async def async_task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_table_report_evaluator()
        summary_name = summary_eval.get_serialization_name()
        exp = llmobs.async_experiment(
            "test_async_exp_table_report_summary",
            async_task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)
        summary_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)
        summary_name = summary_eval.get_serialization_name()
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is not None
        assert (
            summary_entry["error"]["message"]
            == "Pydantic report evaluator returned a non-scalar result; only a scalar result is allowed"
        )

    async def test_async_experiment_rejects_report_analysis_union_annotation(self, llmobs):
        async def async_task(input_data, config):
            return input_data.get("value", "")

        dataset = _dataset_for_report_summary_tests()
        row_eval = _make_simple_pydantic_evaluator()
        summary_eval = _make_report_evaluator_with_analysis_union_annotation()
        summary_name = summary_eval.get_serialization_name()
        exp = llmobs.async_experiment(
            "test_async_exp_wide_report_summary",
            async_task,
            dataset,
            [row_eval],
            summary_evaluators=[summary_eval],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = await exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = await exp._run_evaluators(task_results, raise_errors=False)
        summary_results = await exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)
        summary_entry = summary_results[0]["evaluations"][summary_name]
        assert summary_entry["error"] is None
        assert summary_entry["value"] == 1.0
