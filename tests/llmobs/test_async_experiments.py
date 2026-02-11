"""
Tests for AsyncExperiment.

All tests exercise AsyncExperiment through its public run() method only.

To run these tests, you need to set the following environment variables:

- RECORD_REQUESTS=1  # used to delay tests until data is ready from the backend
- DD_APP_KEY=...  # your datadog application key
- DD_API_KEY=...  # your datadog api key

and must have the test agent (>=1.27.0) running locally and configured to use the vcr cassette directory

eg. VCR_CASSETTES_DIRECTORY=tests/cassettes ddapm-test-agent ...

To run these tests:
    dd-auth -- riot -v run --pass-env -s -p 3.12 llmobs -- -s -vv -k 'test_async_experiments.py'
"""

import asyncio
from contextlib import asynccontextmanager
import os
import time
from typing import Generator
from unittest import mock

import pytest

from ddtrace.llmobs._async_experiment import AsyncExperiment
from ddtrace.llmobs._async_experiment import BaseAsyncEvaluator
from ddtrace.llmobs._async_experiment import BaseAsyncSummaryEvaluator
from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs._experiment import _ExperimentRunInfo


def wait_for_backend(sleep_dur=2):
    """Wait for backend when recording requests."""
    if os.environ.get("RECORD_REQUESTS", "0") != "0":
        time.sleep(sleep_dur)


# --- Async Task Functions ---


async def async_dummy_task(input_data, config):
    """Simple async task that returns input_data."""
    return input_data


async def async_faulty_task(input_data, config):
    """Async task that raises an error."""
    raise ValueError("This is a test error")


# --- Sync Evaluator Functions ---


def sync_dummy_evaluator(input_data, output_data, expected_output):
    """Sync evaluator that returns whether output matches expected."""
    return int(output_data == expected_output)


def sync_faulty_evaluator(input_data, output_data, expected_output):
    """Sync evaluator that raises an error."""
    raise ValueError("This is a test error in evaluator")


# --- Async Evaluator Functions ---


async def async_dummy_evaluator(input_data, output_data, expected_output):
    """Async evaluator that returns whether output matches expected."""
    await asyncio.sleep(0.001)  # Small delay to ensure async behavior
    return int(output_data == expected_output)


async def async_faulty_evaluator(input_data, output_data, expected_output):
    """Async evaluator that raises an error."""
    raise ValueError("This is a test error in async evaluator")


async def async_evaluator_with_extra_return_values(input_data, output_data, expected_output):
    """Async evaluator that returns EvaluatorResult with extra values."""
    await asyncio.sleep(0.001)
    return EvaluatorResult(
        value=expected_output == output_data,
        reasoning="async match" if expected_output == output_data else "async no match",
        assessment="pass" if expected_output == output_data else "fail",
        metadata={"async": True},
        tags={"evaluator_type": "async"},
    )


# --- Summary Evaluator Functions ---


def sync_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    """Sync summary evaluator."""
    return len(inputs) + len(outputs) + len(expected_outputs)


async def async_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    """Async summary evaluator."""
    await asyncio.sleep(0.001)
    return len(inputs) + len(outputs) + len(expected_outputs)


async def async_faulty_summary_evaluator(inputs, outputs, expected_outputs, evaluators_results):
    """Async summary evaluator that raises an error."""
    raise ValueError("This is a test error in async summary evaluator")


# --- Class-based Evaluators ---


class SyncClassEvaluator(BaseEvaluator):
    """Sync class-based evaluator."""

    def __init__(self):
        super().__init__(name="sync_class_evaluator")

    def evaluate(self, context: EvaluatorContext):
        return int(context.output_data == context.expected_output)


class AsyncClassEvaluator(BaseAsyncEvaluator):
    """Async class-based evaluator."""

    def __init__(self):
        super().__init__(name="async_class_evaluator")

    async def evaluate(self, context: EvaluatorContext):
        await asyncio.sleep(0.001)
        return int(context.output_data == context.expected_output)


class SyncClassSummaryEvaluator(BaseSummaryEvaluator):
    """Sync class-based summary evaluator."""

    def __init__(self):
        super().__init__(name="sync_class_summary_evaluator")

    def evaluate(self, context: SummaryEvaluatorContext):
        return sum(len(lst) for lst in [context.inputs, context.outputs, context.expected_outputs])


class AsyncClassSummaryEvaluator(BaseAsyncSummaryEvaluator):
    """Async class-based summary evaluator."""

    def __init__(self):
        super().__init__(name="async_class_summary_evaluator")

    async def evaluate(self, context: SummaryEvaluatorContext):
        await asyncio.sleep(0.001)
        return sum(len(lst) for lst in [context.inputs, context.outputs, context.expected_outputs])


# --- Test Helpers ---


STABLE_SPAN_ID = "123"
STABLE_TRACE_ID = "456"
# Timestamp must be within 24hrs for API validation. Update this when re-recording cassettes.
STABLE_TIMESTAMP = 1770751160226390000  # Feb 10, 2026 in nanoseconds


def _stable_run_info(iteration: int) -> _ExperimentRunInfo:
    """Create an _ExperimentRunInfo with a stable ID for testing."""
    run_info = _ExperimentRunInfo(iteration)
    run_info._id = "12345678-abcd-abcd-abcd-123456789012"
    return run_info


@asynccontextmanager
async def stable_experiment_run(exp: AsyncExperiment):
    """Context manager that fixes non-deterministic values for stable cassette recordings.

    Patches:
    - ddtrace.version tag (to prevent cassette hash mismatches across versions)
    - Run UUIDs (via _ExperimentRunInfo)
    - Span/Trace IDs and timestamps (via _run_task_for_record wrapper)
    - Metrics posting (mocked to avoid timestamp validation while still returning results)

    All other execution (tasks, evaluators, summary evaluators) proceeds normally.

    Example:
        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1)
    """
    # Fix ddtrace.version to a stable value to ensure deterministic cassette hashes
    exp._tags["ddtrace.version"] = "1.2.3"

    original_run_task = exp._run_task_for_record

    async def patched_run_task(record, idx, run, iteration_tags, semaphore, raise_errors):
        """Wrapper that calls the real _run_task_for_record but returns stable IDs/timestamps."""
        output_data, _, _, _, error_dict = await original_run_task(
            record, idx, run, iteration_tags, semaphore, raise_errors
        )
        # Return stable span_id, trace_id, and timestamp for deterministic cassettes
        return output_data, STABLE_SPAN_ID, STABLE_TRACE_ID, STABLE_TIMESTAMP, error_dict

    with mock.patch(
        "ddtrace.llmobs._async_experiment._ExperimentRunInfo",
        side_effect=_stable_run_info,
    ):
        with mock.patch.object(exp, "_run_task_for_record", side_effect=patched_run_task):
            yield


# --- Fixtures ---


@pytest.fixture
def async_test_dataset_one_record(llmobs) -> Generator[Dataset, None, None]:
    """Dataset with a single record for async tests."""
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
        )
    ]
    ds = llmobs.create_dataset(dataset_name="async-test-dataset-one", description="A test dataset", records=records)

    # When recording the requests, we need to wait for the dataset to be queryable.
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def async_test_dataset_multiple_records(llmobs) -> Generator[Dataset, None, None]:
    """Dataset with multiple records for async tests."""
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
        ),
        DatasetRecord(
            input_data={"prompt": "What is the capital of Germany?"},
            expected_output={"answer": "Berlin"},
        ),
        DatasetRecord(
            input_data={"prompt": "What is the capital of Japan?"},
            expected_output={"answer": "Tokyo"},
        ),
    ]
    ds = llmobs.create_dataset(
        dataset_name="async-test-dataset-multiple", description="A test dataset with multiple records", records=records
    )

    # When recording the requests, we need to wait for the dataset to be queryable.
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


@pytest.fixture
def async_test_dataset_with_metadata(llmobs) -> Generator[Dataset, None, None]:
    """Dataset with metadata for async tests."""
    records = [
        DatasetRecord(
            input_data={"prompt": "What is the capital of France?"},
            expected_output={"answer": "Paris"},
            metadata={"difficulty": "easy"},
        )
    ]
    ds = llmobs.create_dataset(
        dataset_name="async-test-dataset-metadata", description="A test dataset with metadata", records=records
    )

    # When recording the requests, we need to wait for the dataset to be queryable.
    wait_for_backend()

    yield ds

    llmobs._delete_dataset(dataset_id=ds._id)


# --- Initialization Tests ---


class TestAsyncExperimentInit:
    """Tests for AsyncExperiment initialization."""

    def test_async_experiment_invalid_task_type_raises(self, llmobs, async_test_dataset_one_record):
        """Non-async task should raise TypeError."""
        with pytest.raises(TypeError, match="task must be an async function"):
            llmobs.async_experiment(
                "test_experiment",
                lambda x, y: x,  # sync function, not async
                async_test_dataset_one_record,
                [async_dummy_evaluator],
            )

    def test_async_experiment_invalid_task_signature_raises(self, llmobs, async_test_dataset_one_record):
        """Task with wrong signature should raise TypeError."""

        async def bad_task(x):  # missing 'config' parameter
            return x

        with pytest.raises(TypeError, match="input_data.*config"):
            llmobs.async_experiment(
                "test_experiment",
                bad_task,
                async_test_dataset_one_record,
                [async_dummy_evaluator],
            )

    def test_async_experiment_invalid_dataset_raises(self, llmobs):
        """Non-Dataset object should raise TypeError."""
        with pytest.raises(TypeError, match="Dataset must be an LLMObs Dataset"):
            llmobs.async_experiment(
                "test_experiment",
                async_dummy_task,
                [{"input": "data"}],  # not a Dataset
                [async_dummy_evaluator],
            )

    def test_async_experiment_empty_evaluators_raises(self, llmobs, async_test_dataset_one_record):
        """Empty evaluators list should raise TypeError."""
        with pytest.raises(TypeError, match="Evaluators must be a non-empty list"):
            llmobs.async_experiment(
                "test_experiment",
                async_dummy_task,
                async_test_dataset_one_record,
                [],
            )

    def test_async_experiment_invalid_evaluator_signature_raises(self, llmobs, async_test_dataset_one_record):
        """Evaluator with wrong signature should raise TypeError."""

        async def bad_evaluator(x):  # missing required parameters
            return x

        with pytest.raises(TypeError, match="input_data.*output_data.*expected_output"):
            llmobs.async_experiment(
                "test_experiment",
                async_dummy_task,
                async_test_dataset_one_record,
                [bad_evaluator],
            )

    def test_async_experiment_init_success(self, llmobs, async_test_dataset_one_record):
        """Valid initialization should succeed."""
        exp = llmobs.async_experiment(
            "test_experiment",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
            description="Test experiment",
            tags={"env": "test"},
            config={"model": "test"},
        )
        assert exp.name == "test_experiment"
        assert exp._description == "Test experiment"
        assert isinstance(exp, AsyncExperiment)


# --- Run Tests ---


class TestAsyncExperimentRun:
    """Tests for AsyncExperiment.run() method."""

    @pytest.mark.asyncio
    async def test_async_experiment_run_basic(self, llmobs, async_test_dataset_one_record):
        """Basic experiment run with async task and evaluator."""
        exp = llmobs.async_experiment(
            "test_async_experiment_basic",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1)

        assert "runs" in result
        assert len(result["runs"]) == 1
        assert len(result["runs"][0].rows) == 1

        row = result["runs"][0].rows[0]
        assert row["input"] == {"prompt": "What is the capital of France?"}
        assert row["output"] == {"prompt": "What is the capital of France?"}
        assert "async_dummy_evaluator" in row["evaluations"]

    @pytest.mark.asyncio
    async def test_async_experiment_run_multiple_records(self, llmobs, async_test_dataset_multiple_records):
        """Experiment run with multiple records."""
        exp = llmobs.async_experiment(
            "test_async_experiment_multiple",
            async_dummy_task,
            async_test_dataset_multiple_records,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=3)

        assert len(result["runs"]) == 1
        assert len(result["runs"][0].rows) == 3

    @pytest.mark.asyncio
    async def test_async_experiment_run_with_sync_evaluator(self, llmobs, async_test_dataset_one_record):
        """Async experiment with sync evaluator (run in thread)."""
        exp = llmobs.async_experiment(
            "test_async_experiment_sync_eval",
            async_dummy_task,
            async_test_dataset_one_record,
            [sync_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1)

        assert len(result["runs"][0].rows) == 1
        assert "sync_dummy_evaluator" in result["runs"][0].rows[0]["evaluations"]

    @pytest.mark.asyncio
    async def test_async_experiment_run_mixed_evaluators(self, llmobs, async_test_dataset_one_record):
        """Experiment with mix of sync and async evaluators."""
        exp = llmobs.async_experiment(
            "test_async_experiment_mixed_eval",
            async_dummy_task,
            async_test_dataset_one_record,
            [sync_dummy_evaluator, async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=2)

        row = result["runs"][0].rows[0]
        assert "sync_dummy_evaluator" in row["evaluations"]
        assert "async_dummy_evaluator" in row["evaluations"]

    @pytest.mark.asyncio
    async def test_async_experiment_run_with_class_evaluators(self, llmobs, async_test_dataset_one_record):
        """Experiment with class-based evaluators (sync and async)."""
        exp = llmobs.async_experiment(
            "test_async_experiment_class_eval",
            async_dummy_task,
            async_test_dataset_one_record,
            [SyncClassEvaluator(), AsyncClassEvaluator()],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=2)

        row = result["runs"][0].rows[0]
        assert "sync_class_evaluator" in row["evaluations"]
        assert "async_class_evaluator" in row["evaluations"]

    @pytest.mark.asyncio
    async def test_async_experiment_run_evaluator_extra_return_values(self, llmobs, async_test_dataset_one_record):
        """Experiment with evaluator returning EvaluatorResult."""
        exp = llmobs.async_experiment(
            "test_async_experiment_extra_values",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_evaluator_with_extra_return_values],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1)

        row = result["runs"][0].rows[0]
        eval_result = row["evaluations"]["async_evaluator_with_extra_return_values"]
        assert "reasoning" in eval_result
        assert "assessment" in eval_result
        assert "metadata" in eval_result
        assert "tags" in eval_result


# --- Error Handling Tests ---


class TestAsyncExperimentErrorHandling:
    """Tests for error handling in AsyncExperiment."""

    @pytest.mark.asyncio
    async def test_async_experiment_task_error_no_raise(self, llmobs, async_test_dataset_one_record):
        """Task error captured without raising (raise_errors=False)."""
        exp = llmobs.async_experiment(
            "test_async_experiment_task_error",
            async_faulty_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1, raise_errors=False)

        row = result["runs"][0].rows[0]
        assert row["error"]["message"] is not None
        assert "test error" in row["error"]["message"]

    @pytest.mark.asyncio
    async def test_async_experiment_task_error_raises(self, llmobs, async_test_dataset_one_record):
        """Task error propagated when raise_errors=True."""
        exp = llmobs.async_experiment(
            "test_async_experiment_task_error_raise",
            async_faulty_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            with pytest.raises(RuntimeError, match="Error on record"):
                await exp.run(jobs=1, raise_errors=True)

    @pytest.mark.asyncio
    async def test_async_experiment_evaluator_error_no_raise(self, llmobs, async_test_dataset_one_record):
        """Evaluator error captured without raising."""
        exp = llmobs.async_experiment(
            "test_async_experiment_eval_error",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_faulty_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1, raise_errors=False)

        row = result["runs"][0].rows[0]
        eval_result = row["evaluations"]["async_faulty_evaluator"]
        assert eval_result["error"] is not None
        assert "test error" in eval_result["error"]["message"]

    @pytest.mark.asyncio
    async def test_async_experiment_evaluator_error_raises(self, llmobs, async_test_dataset_one_record):
        """Evaluator error propagated when raise_errors=True."""
        exp = llmobs.async_experiment(
            "test_async_experiment_eval_error_raise",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_faulty_evaluator],
        )

        async with stable_experiment_run(exp):
            with pytest.raises(RuntimeError, match="Evaluator.*failed"):
                await exp.run(jobs=1, raise_errors=True)


# --- Summary Evaluator Tests ---


class TestAsyncExperimentSummaryEvaluators:
    """Tests for summary evaluators in AsyncExperiment."""

    @pytest.mark.asyncio
    async def test_async_experiment_with_sync_summary_evaluator(self, llmobs, async_test_dataset_multiple_records):
        """Experiment with sync summary evaluator."""
        exp = llmobs.async_experiment(
            "test_async_experiment_sync_summary",
            async_dummy_task,
            async_test_dataset_multiple_records,
            [async_dummy_evaluator],
            summary_evaluators=[sync_summary_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=3)

        summary = result["runs"][0].summary_evaluations
        assert "sync_summary_evaluator" in summary
        # 3 inputs + 3 outputs + 3 expected_outputs = 9
        assert summary["sync_summary_evaluator"]["value"] == 9

    @pytest.mark.asyncio
    async def test_async_experiment_with_async_summary_evaluator(self, llmobs, async_test_dataset_multiple_records):
        """Experiment with async summary evaluator."""
        exp = llmobs.async_experiment(
            "test_async_experiment_async_summary",
            async_dummy_task,
            async_test_dataset_multiple_records,
            [async_dummy_evaluator],
            summary_evaluators=[async_summary_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=3)

        summary = result["runs"][0].summary_evaluations
        assert "async_summary_evaluator" in summary
        assert summary["async_summary_evaluator"]["value"] == 9

    @pytest.mark.asyncio
    async def test_async_experiment_with_class_summary_evaluators(self, llmobs, async_test_dataset_multiple_records):
        """Experiment with class-based summary evaluators."""
        exp = llmobs.async_experiment(
            "test_async_experiment_class_summary",
            async_dummy_task,
            async_test_dataset_multiple_records,
            [async_dummy_evaluator],
            summary_evaluators=[SyncClassSummaryEvaluator(), AsyncClassSummaryEvaluator()],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=3)

        summary = result["runs"][0].summary_evaluations
        assert "sync_class_summary_evaluator" in summary
        assert "async_class_summary_evaluator" in summary

    @pytest.mark.asyncio
    async def test_async_experiment_summary_evaluator_error_no_raise(self, llmobs, async_test_dataset_one_record):
        """Summary evaluator error captured without raising."""
        exp = llmobs.async_experiment(
            "test_async_experiment_summary_error",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
            summary_evaluators=[async_faulty_summary_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1, raise_errors=False)

        summary = result["runs"][0].summary_evaluations
        assert "async_faulty_summary_evaluator" in summary
        assert summary["async_faulty_summary_evaluator"]["error"] is not None

    @pytest.mark.asyncio
    async def test_async_experiment_summary_evaluator_error_raises(self, llmobs, async_test_dataset_one_record):
        """Summary evaluator error propagated when raise_errors=True."""
        exp = llmobs.async_experiment(
            "test_async_experiment_summary_error_raise",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
            summary_evaluators=[async_faulty_summary_evaluator],
        )

        async with stable_experiment_run(exp):
            with pytest.raises(RuntimeError, match="Summary evaluator.*failed"):
                await exp.run(jobs=1, raise_errors=True)


# --- Multiple Runs Tests ---


class TestAsyncExperimentMultipleRuns:
    """Tests for multiple experiment runs."""

    @pytest.mark.asyncio
    async def test_async_experiment_multiple_runs(self, llmobs, async_test_dataset_one_record):
        """Experiment with multiple runs (all run concurrently)."""
        exp = llmobs.async_experiment(
            "test_async_experiment_multi_run",
            async_dummy_task,
            async_test_dataset_one_record,
            [async_dummy_evaluator],
            runs=3,
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=3)

        assert len(result["runs"]) == 3
        # Each run should have its own iteration number
        iterations = {run.run_iteration for run in result["runs"]}
        assert iterations == {1, 2, 3}


# --- Sample Size Tests ---


class TestAsyncExperimentSampleSize:
    """Tests for sample_size parameter."""

    @pytest.mark.asyncio
    async def test_async_experiment_sample_size(self, llmobs, async_test_dataset_multiple_records):
        """Experiment with sample_size limits records processed."""
        exp = llmobs.async_experiment(
            "test_async_experiment_sample",
            async_dummy_task,
            async_test_dataset_multiple_records,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1, sample_size=2)

        # Only 2 records should be processed
        assert len(result["runs"][0].rows) == 2


# --- Metadata Tests ---


class TestAsyncExperimentMetadata:
    """Tests for metadata handling."""

    @pytest.mark.asyncio
    async def test_async_experiment_with_record_metadata(self, llmobs, async_test_dataset_with_metadata):
        """Experiment correctly handles record metadata."""
        exp = llmobs.async_experiment(
            "test_async_experiment_metadata",
            async_dummy_task,
            async_test_dataset_with_metadata,
            [async_dummy_evaluator],
        )

        async with stable_experiment_run(exp):
            result = await exp.run(jobs=1)

        row = result["runs"][0].rows[0]
        assert "metadata" in row
        assert "tags" in row["metadata"]
