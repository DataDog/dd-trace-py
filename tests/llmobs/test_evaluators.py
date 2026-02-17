"""Tests for LLMObs evaluator classes."""

from unittest import mock

import pytest

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import RemoteEvaluator
from ddtrace.llmobs._experiment import RemoteEvaluatorError
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs._experiment import _ExperimentRunInfo


class SimpleEvaluator(BaseEvaluator):
    """A simple test evaluator that checks if output equals expected."""

    def evaluate(self, context: EvaluatorContext):
        passed = context.output_data == context.expected_output
        return {"passed": passed, "score": 1.0 if passed else 0.0}


class PrimitiveEvaluator(BaseEvaluator):
    """An evaluator that returns primitive types (like function-based evaluators)."""

    def evaluate(self, context: EvaluatorContext):
        # Return int like function-based evaluators can do
        return int(context.output_data == context.expected_output)


class StatefulEvaluator(BaseEvaluator):
    """An evaluator with internal state."""

    def __init__(self, threshold=0.5):
        super().__init__()
        self.threshold = threshold
        self.call_count = 0

    def evaluate(self, context: EvaluatorContext):
        self.call_count += 1
        score = float(context.output_data == context.expected_output)
        return {
            "passed": score >= self.threshold,
            "score": score,
            "call_count": self.call_count,
        }


class TestEvaluatorContext:
    def test_context_creation(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
        )
        assert ctx.input_data == {"query": "test"}
        assert ctx.output_data == "response"
        assert ctx.expected_output == "expected"
        assert ctx.metadata == {}
        assert ctx.span_id is None
        assert ctx.trace_id is None

    def test_context_with_optional_fields(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
            metadata={"key": "value", "experiment_config": {"temperature": 0.7}},
            span_id="span_123",
            trace_id="trace_456",
        )
        assert ctx.metadata == {"key": "value", "experiment_config": {"temperature": 0.7}}
        assert ctx.span_id == "span_123"
        assert ctx.trace_id == "trace_456"
        assert ctx.metadata.get("experiment_config") == {"temperature": 0.7}

    def test_context_is_frozen(self):
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
        )
        with pytest.raises(Exception):  # FrozenInstanceError in Python 3.10+
            ctx.output_data = "new_value"


class TestBaseEvaluator:
    def test_evaluator_name_default(self):
        evaluator = SimpleEvaluator()
        assert evaluator.name == "SimpleEvaluator"

    def test_evaluator_name_custom(self):
        evaluator = SimpleEvaluator(name="custom_name")
        assert evaluator.name == "custom_name"

    def test_evaluator_evaluate(self):
        evaluator = SimpleEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result == {"passed": True, "score": 1.0}

    def test_evaluator_name_validation_invalid_characters(self):
        """Test that names with invalid characters are rejected."""
        with pytest.raises(ValueError, match="Evaluator name .* is invalid"):
            SimpleEvaluator(name="my-evaluator")

    def test_evaluator_name_validation_valid_characters(self):
        """Test that valid names are accepted."""
        evaluator = SimpleEvaluator(name="my_evaluator_123")
        assert evaluator.name == "my_evaluator_123"

    def test_evaluator_primitive_return_type(self):
        """Test that evaluators can return primitive types like function-based evaluators."""
        evaluator = PrimitiveEvaluator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1  # Returns int directly

        ctx2 = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="different",
        )
        result2 = evaluator.evaluate(ctx2)
        assert result2 == 0

    def test_stateful_evaluator(self):
        evaluator = StatefulEvaluator(threshold=0.8)
        assert evaluator.threshold == 0.8
        assert evaluator.call_count == 0

        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="response",
        )
        result = evaluator.evaluate(ctx)
        assert result["call_count"] == 1
        assert result["passed"] is True

        # Call again
        result = evaluator.evaluate(ctx)
        assert result["call_count"] == 2


class TestEvaluatorIntegration:
    """Test evaluators with the experiment system."""

    def test_class_evaluator_with_experiment(self, llmobs):
        """Test that class-based evaluators work with experiments."""

        def dummy_task(input_data, config):
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

        evaluator = SimpleEvaluator()
        exp = llmobs.experiment("test_experiment", dummy_task, dataset, [evaluator])

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        assert "SimpleEvaluator" in eval_results[0]["evaluations"]
        result = eval_results[0]["evaluations"]["SimpleEvaluator"]
        assert result["error"] is None
        assert result["value"]["passed"] is True

    def test_mixed_evaluators_with_experiment(self, llmobs):
        """Test mixing function and class-based evaluators."""

        def dummy_task(input_data, config):
            return input_data.get("value", "")

        def function_evaluator(input_data, output_data, expected_output):
            return {"type": "function", "passed": output_data == expected_output}

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

        class_evaluator = SimpleEvaluator()
        exp = llmobs.experiment(
            "test_experiment",
            dummy_task,
            dataset,
            [function_evaluator, class_evaluator],
        )

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)

        assert len(eval_results) == 1
        evaluations = eval_results[0]["evaluations"]
        assert "function_evaluator" in evaluations
        assert "SimpleEvaluator" in evaluations
        assert evaluations["function_evaluator"]["value"]["type"] == "function"
        assert evaluations["SimpleEvaluator"]["value"]["passed"] is True


class SimpleSummaryEvaluator(BaseSummaryEvaluator):
    """A simple test summary evaluator."""

    def evaluate(self, context: SummaryEvaluatorContext):
        return len(context.inputs) + len(context.outputs)


class TestSummaryEvaluatorContext:
    def test_context_creation(self):
        ctx = SummaryEvaluatorContext(
            inputs=[{"query": "test"}],
            outputs=["response"],
            expected_outputs=["expected"],
            evaluation_results={"eval1": [1.0]},
        )
        assert ctx.inputs == [{"query": "test"}]
        assert ctx.outputs == ["response"]
        assert ctx.evaluation_results == {"eval1": [1.0]}


class TestSummaryEvaluatorIntegration:
    def test_class_summary_evaluator(self, llmobs):
        def dummy_task(input_data, config):
            return input_data.get("value", "")

        dataset = Dataset(
            name="test_dataset",
            project={"name": "test_project", "_id": "proj_123"},
            dataset_id="ds_123",
            records=[
                {"record_id": "rec_1", "input_data": {"value": "test"}, "expected_output": "test", "metadata": {}}
            ],
            description="",
            latest_version=1,
            version=1,
            _dne_client=None,
        )

        exp = llmobs.experiment(
            "test_experiment", dummy_task, dataset, [SimpleEvaluator()], summary_evaluators=[SimpleSummaryEvaluator()]
        )

        run_info = _ExperimentRunInfo(0)
        task_results = exp._run_task(1, run=run_info, raise_errors=False)
        eval_results = exp._run_evaluators(task_results, raise_errors=False)
        summary_results = exp._run_summary_evaluators(task_results, eval_results, raise_errors=False)

        assert "SimpleSummaryEvaluator" in summary_results[0]["evaluations"]
        assert summary_results[0]["evaluations"]["SimpleSummaryEvaluator"]["value"] == 2


class TestRemoteEvaluator:
    """Tests for RemoteEvaluator class."""

    def test_init_valid(self):
        """Test valid RemoteEvaluator initialization."""

        def transform(ctx):
            return {"input": ctx.input_data}

        mock_client = mock.MagicMock()
        evaluator = RemoteEvaluator(
            eval_name="my-evaluator",
            transform_fn=transform,
            _client=mock_client,
        )
        assert evaluator.name == "my-evaluator"
        assert evaluator._eval_name == "my-evaluator"
        assert evaluator._transform_fn == transform
        assert evaluator._client == mock_client

    def test_init_with_hyphens_in_name(self):
        """Test that eval_name can contain hyphens (unlike BaseEvaluator)."""

        def transform(ctx):
            return {}

        evaluator = RemoteEvaluator(
            eval_name="my-evaluator-with-hyphens",
            transform_fn=transform,
            _client=mock.MagicMock(),
        )
        assert evaluator.name == "my-evaluator-with-hyphens"

    def test_init_default_transform(self):
        """Test that default transform is used when transform_fn is None."""
        evaluator = RemoteEvaluator(
            eval_name="my-evaluator",
            _client=mock.MagicMock(),
        )
        assert evaluator._transform_fn is not None

        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
            expected_output="expected",
        )
        result = evaluator._transform_fn(ctx)
        assert result == {
            "span_input": {"query": "test"},
            "span_output": "response",
            "meta": {
                "expected_output": "expected",
            },
        }

        ctx_messages = EvaluatorContext(
            input_data={"messages": [{"role": "user", "content": "hello"}]},
            output_data={"messages": [{"role": "assistant", "content": "hi"}]},
            span_id="span123",
            trace_id="trace456",
            metadata={"experiment_config": {"temperature": 0.7}},
        )
        result_messages = evaluator._transform_fn(ctx_messages)
        assert result_messages == {
            "span_input": {"messages": [{"role": "user", "content": "hello"}]},
            "span_output": {"messages": [{"role": "assistant", "content": "hi"}]},
            "meta": {
                "metadata": {"experiment_config": {"temperature": 0.7}},
            },
            "span_id": "span123",
            "trace_id": "trace456",
        }

    def test_init_empty_eval_name(self):
        """Test that empty eval_name raises ValueError."""
        with pytest.raises(ValueError, match="eval_name must be a non-empty string"):
            RemoteEvaluator(eval_name="", _client=mock.MagicMock())

    def test_init_non_string_eval_name(self):
        """Test that non-string eval_name raises ValueError."""
        with pytest.raises(ValueError, match="eval_name must be a non-empty string"):
            RemoteEvaluator(eval_name=123, _client=mock.MagicMock())  # type: ignore

    def test_init_non_callable_transform(self):
        """Test that non-callable transform_fn raises TypeError."""
        with pytest.raises(TypeError, match="transform_fn must be callable"):
            RemoteEvaluator(eval_name="eval", transform_fn="not-callable", _client=mock.MagicMock())  # type: ignore

    def test_evaluate_success_with_score(self):
        """Test successful evaluation returning score."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.return_value = {
            "status": "OK",
            "value": 0.95,
            "assessment": "pass",
            "reasoning": "Great response",
        }

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {"input": ctx.input_data},
            _client=mock_client,
        )

        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="response",
        )
        result = evaluator.evaluate(ctx)

        assert isinstance(result, EvaluatorResult)
        assert result.value == 0.95
        assert result.reasoning == "Great response"
        assert result.assessment == "pass"

        mock_client.evaluator_infer.assert_called_once_with(
            eval_name="test-eval",
            context={"input": {"query": "test"}},
        )

    def test_evaluate_success_with_categorical_value(self):
        """Test evaluation returning categorical value."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.return_value = {
            "status": "OK",
            "value": "good",
            "assessment": None,
            "reasoning": None,
        }

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={}, output_data="")
        result = evaluator.evaluate(ctx)

        assert result == "good"

    def test_evaluate_transform_error(self):
        """Test that transform_fn errors propagate naturally."""

        def bad_transform(ctx):
            raise ValueError("Transform failed")

        mock_client = mock.MagicMock()
        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=bad_transform,
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={}, output_data="")

        with pytest.raises(ValueError) as exc_info:
            evaluator.evaluate(ctx)

        assert "Transform failed" in str(exc_info.value)

    def test_evaluate_backend_error(self):
        """Test that backend errors are propagated."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.side_effect = RemoteEvaluatorError(
            "Backend error", backend_error={"type": "invalid_config", "message": "Bad config"}
        )

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={}, output_data="")

        with pytest.raises(RemoteEvaluatorError) as exc_info:
            evaluator.evaluate(ctx)

        assert exc_info.value.backend_error["type"] == "invalid_config"

    def test_evaluate_http_error(self):
        """Test that HTTP errors raise RuntimeError."""
        mock_client = mock.MagicMock()
        # Simulate evaluator_infer raising RuntimeError for HTTP error
        mock_client.evaluator_infer.side_effect = RuntimeError("Failed to call evaluator 'test-eval': HTTP 500")

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={}, output_data="")

        with pytest.raises(RuntimeError) as exc_info:
            evaluator.evaluate(ctx)

        assert "Failed to call evaluator 'test-eval': HTTP 500" in str(exc_info.value)

    def test_evaluate_llmobs_not_enabled(self, llmobs):
        """Test that AttributeError is raised if LLMObs not enabled."""
        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {},
            _client=None,
        )

        with mock.patch("ddtrace.llmobs.LLMObs._instance", None):
            ctx = EvaluatorContext(input_data={}, output_data="")

            with pytest.raises(AttributeError) as exc_info:
                evaluator.evaluate(ctx)

            assert "'NoneType' object has no attribute '_dne_client'" in str(exc_info.value)

    def test_is_remote_evaluator_marker(self):
        """Test that RemoteEvaluator has _is_remote_evaluator marker."""
        evaluator = RemoteEvaluator(
            eval_name="test",
            _client=mock.MagicMock(),
        )
        assert evaluator._is_remote_evaluator is True

    def test_evaluate_warn_status_raises_error(self):
        """Test that WARN status from backend raises RemoteEvaluatorError."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.side_effect = RemoteEvaluatorError(
            "Remote evaluator 'test-eval' failed: Evaluation was skipped",
            backend_error={
                "type": "EVALUATION_SKIPPED",
                "message": "Evaluation was skipped due to rate limiting",
                "recommended_resolution": "Reduce evaluation frequency or increase rate limits",
            },
        )

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {"input": ctx.input_data},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={"query": "test"}, output_data="response")

        with pytest.raises(RemoteEvaluatorError) as exc_info:
            evaluator.evaluate(ctx)

        assert "Evaluation was skipped" in str(exc_info.value)
        assert exc_info.value.backend_error["type"] == "EVALUATION_SKIPPED"
        assert exc_info.value.backend_error["message"] == "Evaluation was skipped due to rate limiting"
        assert "rate limits" in exc_info.value.backend_error["recommended_resolution"]

    def test_evaluate_error_status_raises_error(self):
        """Test that ERROR status from backend raises RemoteEvaluatorError."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.side_effect = RemoteEvaluatorError(
            "Remote evaluator 'test-eval' failed: API key not configured",
            backend_error={
                "type": "API_KEY_MISSING",
                "message": "API key not configured for provider OpenAI",
                "recommended_resolution": "Add API key in Datadog integrations settings",
            },
        )

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {"input": ctx.input_data},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={"query": "test"}, output_data="response")

        with pytest.raises(RemoteEvaluatorError) as exc_info:
            evaluator.evaluate(ctx)

        assert "API key not configured" in str(exc_info.value)
        assert exc_info.value.backend_error["type"] == "API_KEY_MISSING"
        assert exc_info.value.backend_error["message"] == "API key not configured for provider OpenAI"
        assert "integrations settings" in exc_info.value.backend_error["recommended_resolution"]

    def test_evaluate_http_404_jsonapi_error(self):
        """Test that HTTP 404 errors raise RuntimeError with parsed message."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.side_effect = RuntimeError(
            "Failed to call evaluator 'missing-eval': Evaluator not found in organization"
        )

        evaluator = RemoteEvaluator(
            eval_name="missing-eval",
            transform_fn=lambda ctx: {"input": ctx.input_data},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={"query": "test"}, output_data="response")

        with pytest.raises(RuntimeError) as exc_info:
            evaluator.evaluate(ctx)

        assert "Evaluator not found" in str(exc_info.value)

    def test_evaluate_http_500_jsonapi_error(self):
        """Test that HTTP 500 errors raise RuntimeError with parsed message."""
        mock_client = mock.MagicMock()
        mock_client.evaluator_infer.side_effect = RuntimeError(
            "Failed to call evaluator 'test-eval': Internal server error processing evaluation"
        )

        evaluator = RemoteEvaluator(
            eval_name="test-eval",
            transform_fn=lambda ctx: {"input": ctx.input_data},
            _client=mock_client,
        )

        ctx = EvaluatorContext(input_data={"query": "test"}, output_data="response")

        with pytest.raises(RuntimeError) as exc_info:
            evaluator.evaluate(ctx)

        assert "Internal server error" in str(exc_info.value)
