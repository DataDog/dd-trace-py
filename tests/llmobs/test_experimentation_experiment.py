import concurrent.futures
import json
import os
import time
import uuid
from unittest.mock import patch, MagicMock, call

import pytest
import vcr

import ddtrace.llmobs.experimentation as dne
from ddtrace.llmobs.experimentation._config import _is_locally_initialized, get_base_url
from ddtrace.llmobs.experimentation._experiment import ExperimentResults
from ddtrace.llmobs.experimentation.utils._ui import Color

# Hardcoded VCR credentials (replace for recording)
DD_API_KEY = "replace when recording"
DD_APPLICATION_KEY = "replace when recording"
DD_SITE = "us3.datadoghq.com"


def scrub_response_headers(response):
    headers_to_remove = ["content-security-policy", "strict-transport-security", "set-cookie"]
    for header in headers_to_remove:
        response["headers"].pop(header, None)
    response["headers"]["content-length"] = ["100"] # Mock content length for stability
    return response


@pytest.fixture(scope="module")
def experiments_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "experiments_cassettes"),
        record_mode="new_episodes",  # Change to 'all' or 'new_episodes' for recording
        match_on=["path", "method", "body"],
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY", "Openai-Api-Key", "Authorization"],
        before_record_response=scrub_response_headers,
        ignore_localhost=True,
    )


@pytest.fixture(scope="module", autouse=True)
def init_llmobs(experiments_vcr):
    # Use the provided keys directly. VCR filtering handles redaction.
    api_key = DD_API_KEY
    app_key = DD_APPLICATION_KEY
    try:
        dne.init(
            ml_app="test-exp-app", # Provide the required ML application name
            project_name="Test-Project-Experiment",
            api_key=api_key,
            application_key=app_key,
            site=DD_SITE
        )
    except Exception as e:
        pytest.skip(f"Skipping LLMObs tests: Initialization failed - {e}")

# --- Test Fixtures for Experiment ---

@pytest.fixture
def simple_dataset_data():
    return [
        {"input": "What is 1+1?", "expected_output": "2"},
        {"input": "Capital of France?", "expected_output": "Paris"},
    ]

@pytest.fixture
def simple_local_dataset(simple_dataset_data):
    """A dataset instance that has *not* been pushed."""
    # Use unique name to avoid conflicts if tests run out of order
    name = f"local-exp-ds-{uuid.uuid4().hex[:6]}"
    return dne.Dataset(name=name, data=simple_dataset_data, description="For experiment tests")

@pytest.fixture
def synced_dataset(experiments_vcr, simple_dataset_data):
    """
    Provides a dataset instance that is synchronized with Datadog (has ID/version).
    Uses VCR to pull or create if needed during recording.
    """
    dataset_name = "synced-exp-test-dataset"
    try:
        # Try pulling first - faster if cassette exists
        with experiments_vcr.use_cassette("test_experiment_setup_pull_dataset.yaml"):
             dataset = dne.Dataset.pull(dataset_name)
        assert dataset._datadog_dataset_id is not None
        return dataset
    except ValueError:
        # If pull fails (e.g., first time recording), create and push
        dataset = dne.Dataset(name=dataset_name, data=simple_dataset_data)
        with experiments_vcr.use_cassette("test_experiment_setup_push_dataset.yaml"):
            dataset.push()
        assert dataset._datadog_dataset_id is not None
        return dataset


@dne.task
def simple_identity_task(input):
    """Simple task that returns the input."""
    return input

@dne.task
def task_with_config(input, config):
    """Task that uses config."""
    model_name = config["models"][0]["name"] if config and "models" in config else "default"
    return f"{input} processed by {model_name}"

@dne.task
def failing_task(input):
    """Task that always raises an exception."""
    raise ValueError("Task failed intentionally")

@dne.task
def task_with_sleep(input, duration=0.1):
    """Task that sleeps for a specified duration."""
    time.sleep(duration)
    return f"Slept for {duration}s on input: {input}"

@dne.evaluator
def simple_match_evaluator(input, output, expected_output):
    """Evaluates if output matches expected_output."""
    return output == expected_output

@dne.evaluator
def length_evaluator(input, output, expected_output):
    """Evaluates based on output length."""
    return len(str(output))

@dne.evaluator
def failing_evaluator(input, output, expected_output):
    """Evaluator that always raises an exception."""
    raise TypeError("Evaluator failed intentionally")

@pytest.fixture
def valid_config():
    return {"models": [{"name": "test-model", "provider": "test-provider", "temperature": 0.7}]}

@pytest.fixture
def experiment_after_task_run(synced_dataset, experiments_vcr):
    """Fixture providing an Experiment instance *after* run_task has completed."""
    exp_name = f"exp-after-task-{uuid.uuid4().hex[:6]}"
    exp = dne.Experiment(
        name=exp_name,
        task=simple_identity_task,
        dataset=synced_dataset,
        evaluators=[simple_match_evaluator]
    )
    with experiments_vcr.use_cassette("test_experiment_run_task_setup.yaml"):
        # Mock _is_locally_initialized to False to force Datadog interaction path
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             exp.run_task() # This will create the experiment in DD via VCR
    assert exp.has_run
    assert not exp.has_evaluated
    assert exp._datadog_experiment_id is not None
    return exp


# --- Test Classes ---

class TestExperimentInitialization:
    """Tests for Experiment.__init__."""

    def test_init_success(self, simple_local_dataset):
        """Initialize experiment successfully with basic components."""
        exp = dne.Experiment(
            name="test-init",
            task=simple_identity_task,
            dataset=simple_local_dataset,
            evaluators=[simple_match_evaluator, length_evaluator],
            tags=["init", "success"],
            description="A test experiment",
            metadata={"user": "tester"}
        )
        assert exp.name == "test-init"
        assert exp.task == simple_identity_task
        assert exp.dataset == simple_local_dataset
        assert exp.evaluators == [simple_match_evaluator, length_evaluator]
        assert exp.tags == ["init", "success"]
        assert exp.description == "A test experiment"
        assert exp.metadata == {"user": "tester"}
        assert exp.config is None
        assert not exp.has_run
        assert not exp.has_evaluated
        assert exp._datadog_experiment_id is None

    def test_init_success_with_config(self, simple_local_dataset, valid_config):
        """Initialize experiment successfully with a valid config."""
        exp = dne.Experiment(
            name="test-init-config",
            task=task_with_config, 
            dataset=simple_local_dataset,
            evaluators=[],
            config=valid_config
        )
        assert exp.name == "test-init-config"
        assert exp.config == valid_config 

    def test_init_invalid_task_decorator(self, simple_local_dataset):
        """Raise TypeError if task is not decorated with @task."""
        def undecorated_task(input): return input
        with pytest.raises(TypeError, match="Task function must be decorated with @task decorator"):
            dne.Experiment("bad-task", undecorated_task, simple_local_dataset, [])

    def test_init_invalid_evaluator_decorator(self, simple_local_dataset):
        """Raise TypeError if an evaluator is not decorated with @evaluator."""
        def undecorated_evaluator(input, output, expected_output): return True
        with pytest.raises(TypeError, match="Evaluator 'undecorated_evaluator' must be decorated with @evaluator decorator"):
            dne.Experiment("bad-eval", simple_identity_task, simple_local_dataset, [simple_match_evaluator, undecorated_evaluator])

    @pytest.mark.parametrize(
        "invalid_config, expected_exception, match_pattern",
        [
            ("not-a-dict", TypeError, "When provided, config must be a dictionary"),
            ({"models": "not-a-list"}, TypeError, "config\\[\\'models\\'\\] must be a list"),
            ({"models": [{"name": 123}]}, ValueError, "Invalid model at index 0: Model field 'name' must be of type str"),
            ({"models": [{"provider": False}]}, ValueError, "Invalid model at index 0: Model field 'provider' must be of type str"),
            ({"models": [{"temperature": "hot"}]}, ValueError, "Invalid model at index 0: Model field 'temperature' must be of type"), # Adjusted match
        ],
        ids=[
            "config_not_dict",
            "models_not_list",
            "model_name_not_str",
            "model_provider_not_str",
            "model_temp_not_numeric",
        ]
    )
    def test_init_invalid_config(self, simple_local_dataset, invalid_config, expected_exception, match_pattern):
        """Raise TypeError or ValueError for various invalid config structures."""
        with pytest.raises(expected_exception, match=match_pattern):
            dne.Experiment(
                "bad-config-test",
                simple_identity_task,
                simple_local_dataset,
                [],
                config=invalid_config
            )



class TestExperimentRunTask:
    """Tests for Experiment.run_task (requires VCR for Datadog interactions)."""

    def _check_output_structure(self, output_item, expected_keys={"idx", "output", "metadata", "error"}):
        assert isinstance(output_item, dict)
        assert set(output_item.keys()) == expected_keys
        assert isinstance(output_item["metadata"], dict)
        # Check for specific metadata keys added in run_task
        assert "timestamp" in output_item["metadata"]
        assert "duration" in output_item["metadata"]
        assert "dataset_record_index" in output_item["metadata"]
        assert "span_id" in output_item["metadata"]
        assert "trace_id" in output_item["metadata"]
        assert isinstance(output_item["error"], dict)
        assert {"message", "stack", "type"} == set(output_item["error"].keys())

    def test_run_task_success_datadog(self, synced_dataset, experiments_vcr):
        """Run task successfully, creating project/experiment in Datadog."""
        exp_name = f"test-run-task-dd-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=simple_identity_task,
            dataset=synced_dataset,
            evaluators=[simple_match_evaluator] 
        )

        # Ensure we hit the Datadog path
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            # Use cassette from fixture
            with experiments_vcr.use_cassette("test_experiment_run_task_setup.yaml"):
                exp.run_task(jobs=1) # Use 1 job for deterministic VCR recording

        assert exp.has_run
        assert not exp.has_evaluated
        assert len(exp.outputs) == len(synced_dataset)
        assert exp._datadog_experiment_id is not None
        assert exp._datadog_project_id is not None
        # Check the name might have been updated by DD API (e.g., suffix added)
        # Note: The name comparison needs to be flexible as the reused cassette
        # might have recorded a different suffixed name from the fixture run.
        # Check if the originally intended name is a prefix.
        assert exp.name.startswith(exp_name.split('-')[0]) # Check prefix before UUID
        # Basic check on output structure
        self._check_output_structure(exp.outputs[0])
        assert exp.outputs[0]["output"] == synced_dataset[0]["input"]
        assert exp.outputs[0]["error"]["message"] is None

    def test_run_task_success_local(self, simple_local_dataset):
        """Run task successfully in simulated local mode."""
        exp = dne.Experiment(
            name="test-run-task-local",
            task=simple_identity_task,
            dataset=simple_local_dataset,
            evaluators=[]
        )
        # Simulate local mode - no DD interaction expected
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=True):
            exp.run_task(jobs=1)

        assert exp.has_run
        assert not exp.has_evaluated
        assert len(exp.outputs) == len(simple_local_dataset)
        assert exp._datadog_experiment_id is None # No DD ID in local mode
        self._check_output_structure(exp.outputs[0])
        assert exp.outputs[0]["error"]["message"] is None

    def test_run_task_with_config(self, synced_dataset, valid_config, experiments_vcr):
        """Ensure task receives config when run."""
        exp_name = f"test-run-task-config-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=task_with_config,
            dataset=synced_dataset,
            evaluators=[],
            config=valid_config
        )
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_with_config.yaml"):
                 exp.run_task(jobs=1)

        assert exp.has_run
        assert len(exp.outputs) == len(synced_dataset)
        # Check output reflects config usage
        expected_model_name = valid_config["models"][0]["name"]
        assert f"processed by {expected_model_name}" in exp.outputs[0]["output"]
        assert exp.outputs[0]["error"]["message"] is None

    def test_run_task_task_error_no_raise(self, synced_dataset, experiments_vcr):
        """Capture task errors when raise_errors=False."""
        exp_name = f"test-run-task-error-no-raise-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=failing_task,
            dataset=synced_dataset,
            evaluators=[]
        )
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_error_no_raise.yaml"):
                 exp.run_task(jobs=1, raise_errors=False)

        assert exp.has_run
        assert len(exp.outputs) == len(synced_dataset)
        # Check error details are captured
        self._check_output_structure(exp.outputs[0])
        assert exp.outputs[0]["output"] is None
        assert exp.outputs[0]["error"]["message"] == "Task failed intentionally"
        assert exp.outputs[0]["error"]["type"] == "ValueError"
        assert "Traceback" in exp.outputs[0]["error"]["stack"]

    def test_run_task_task_error_raise(self, synced_dataset, experiments_vcr):
        """Raise exception from task when raise_errors=True."""
        exp_name = f"test-run-task-error-raise-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=failing_task,
            dataset=synced_dataset,
            evaluators=[]
        )
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_error_raise.yaml"):
                # We expect run_task to raise the underlying error
                with pytest.raises(RuntimeError, match="Error on record 0: Task failed intentionally"):
                     exp.run_task(jobs=1, raise_errors=True)
        # Depending on execution model (e.g., thread pool), has_run might be True even if error raised mid-way
        # assert exp.has_run # This might be true or false depending on exact failure point

    def test_run_task_sample_size(self, synced_dataset, experiments_vcr):
        """Run task on a subset using sample_size."""
        sample_size = 1
        exp_name = f"test-run-task-sample-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=simple_identity_task,
            dataset=synced_dataset,
            evaluators=[]
        )
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_sample.yaml"):
                 exp.run_task(jobs=1, sample_size=sample_size)

        assert exp.has_run
        assert len(exp.outputs) == sample_size 
        self._check_output_structure(exp.outputs[0])

    def test_run_task_concurrency(self, simple_local_dataset):
        """Test that using more jobs speeds up execution for tasks with waits."""
        # Use a small dataset and a task that sleeps
        sleep_duration = 0.1
        num_records = 3
        small_data = [{"input": f"item {i}"} for i in range(num_records)]
        small_dataset = dne.Dataset(name="concurrency-test-ds", data=small_data)
        exp = dne.Experiment(
            name="test-run-task-concurrency",
            task=task_with_sleep, # Using the new task
            dataset=small_dataset,
            evaluators=[]
        )

        # Run in local mode to avoid DD overhead affecting timing significantly
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=True):
            # Time with 1 job
            start_time_1 = time.time()
            exp.run_task(jobs=1)
            end_time_1 = time.time()
            duration_1 = end_time_1 - start_time_1

            # Reset run status to run again
            exp.has_run = False
            exp.outputs = []

            # Time with multiple jobs (equal to number of records)
            start_time_multi = time.time()
            exp.run_task(jobs=num_records)
            end_time_multi = time.time()
            duration_multi = end_time_multi - start_time_multi

        print(f"\nConcurrency test timings: jobs=1 -> {duration_1:.3f}s, jobs={num_records} -> {duration_multi:.3f}s")

        # Assertions:
        # The multi-job run should be significantly faster than the single-job run.
        # It should take slightly longer than a single task's sleep duration due to overhead.
        # The single-job run should take roughly num_records * sleep_duration + overhead.
        assert duration_multi < duration_1
        # Expect multi-job time roughly > sleep_duration and < 2 * sleep_duration
        assert duration_multi > sleep_duration
        assert duration_multi < sleep_duration * 2
        # Expect single-job time roughly > num_records * sleep_duration
        assert duration_1 > num_records * sleep_duration

    def test_run_task_needs_pushed_dataset_datadog(self, simple_local_dataset, experiments_vcr):
        """Raise ValueError if trying to run with non-pushed dataset in DD mode."""
        exp = dne.Experiment(
            name="test-needs-push",
            task=simple_identity_task,
            dataset=simple_local_dataset, # Not pushed, _datadog_dataset_id is None
            evaluators=[]
        )
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_needs_push.yaml"):
                 with pytest.raises(ValueError, match="Dataset must be pushed to Datadog before running the experiment."):
                     exp.run_task()



class TestExperimentRunEvaluations:
    """Tests for Experiment.run_evaluations (requires experiment_after_task_run fixture)."""

    # Helper to check evaluation structure within ExperimentResults or internal state
    def _check_eval_structure(self, eval_item):
         assert isinstance(eval_item, dict)
         assert "idx" in eval_item
         assert "evaluations" in eval_item
         assert isinstance(eval_item["evaluations"], dict)
         for eval_name, result in eval_item["evaluations"].items():
             assert isinstance(result, dict)
             assert {"value", "error"} == set(result.keys())
             if result["error"]:
                 assert isinstance(result["error"], dict)
                 assert {"message", "type", "stack"} == set(result["error"].keys())

    def test_run_evals_success(self, experiment_after_task_run, experiments_vcr):
        """Run evaluations successfully after task completion and push."""
        exp = experiment_after_task_run # Already ran task
        assert exp.has_run
        assert not exp.has_evaluated

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_evals_success.yaml"):
                results = exp.run_evaluations() # Should push evals automatically

        assert exp.has_evaluated
        assert isinstance(results, ExperimentResults)
        assert len(results) == len(exp.outputs)
        # Check internal state just in case
        assert hasattr(exp, 'evaluations') and len(exp.evaluations) == len(exp.outputs)
        self._check_eval_structure(exp.evaluations[0])
        # Example check on specific evaluator result (simple_match_evaluator should be True)
        assert exp.evaluations[0]["evaluations"]["simple_match_evaluator"]["value"] is True
        assert exp.evaluations[0]["evaluations"]["simple_match_evaluator"]["error"] is None

    def test_run_evals_override_evaluators(self, experiment_after_task_run, experiments_vcr):
        """Run evaluations with a different set of evaluators."""
        exp = experiment_after_task_run
        # Override with just length_evaluator
        override_evaluators = [length_evaluator]

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_evals_override.yaml"):
                results = exp.run_evaluations(evaluators=override_evaluators)

        assert exp.has_evaluated
        assert len(results) == len(exp.outputs)
        self._check_eval_structure(exp.evaluations[0])
        # Check only the override evaluator ran
        assert list(exp.evaluations[0]["evaluations"].keys()) == ["length_evaluator"]
        assert exp.evaluations[0]["evaluations"]["length_evaluator"]["error"] is None


    def test_run_evals_evaluator_error_no_raise(self, experiment_after_task_run, experiments_vcr):
        """Capture evaluator errors when raise_errors=False."""
        exp = experiment_after_task_run
        # Use failing evaluator
        exp.evaluators = [simple_match_evaluator, failing_evaluator]

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_evals_error_no_raise.yaml"):
                 results = exp.run_evaluations(raise_errors=False)

        assert exp.has_evaluated
        assert len(results) == len(exp.outputs)
        self._check_eval_structure(exp.evaluations[0])
        # Check good evaluator worked
        assert exp.evaluations[0]["evaluations"]["simple_match_evaluator"]["value"] is True
        assert exp.evaluations[0]["evaluations"]["simple_match_evaluator"]["error"] is None
        # Check failing evaluator captured error
        assert exp.evaluations[0]["evaluations"]["failing_evaluator"]["value"] is None
        assert exp.evaluations[0]["evaluations"]["failing_evaluator"]["error"]["message"] == "Evaluator failed intentionally"
        assert exp.evaluations[0]["evaluations"]["failing_evaluator"]["error"]["type"] == "TypeError"

    def test_run_evals_evaluator_error_raise(self, experiment_after_task_run, experiments_vcr):
        """Raise exception from evaluator when raise_errors=True."""
        exp = experiment_after_task_run
        exp.evaluators = [failing_evaluator] # Only the failing one

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_evals_error_raise.yaml"):
                 # Expect run_evaluations to raise the underlying error
                 with pytest.raises(RuntimeError, match="Evaluator 'failing_evaluator' failed on row 0"):
                      exp.run_evaluations(raise_errors=True)
        # Should still mark as evaluated even if error occurred? Check implementation. Yes, seems so.
        assert exp.has_evaluated

    def test_run_evals_before_run_task(self, synced_dataset):
        """Raise ValueError if run_evaluations called before run_task."""
        exp = dne.Experiment("test-eval-before-run", simple_identity_task, synced_dataset, [simple_match_evaluator])
        assert not exp.has_run
        with pytest.raises(ValueError, match="Task has not been run yet."):
            exp.run_evaluations()



class TestExperimentRunFull:
    """Tests for the combined Experiment.run() method."""

    def test_run_full_success(self, synced_dataset, experiments_vcr):
        """Test full run() successfully executes task and evaluations."""
        exp_name = f"test-run-full-success-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=simple_identity_task,
            dataset=synced_dataset,
            evaluators=[simple_match_evaluator, length_evaluator]
        )

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_full_success.yaml"):
                results = exp.run(jobs=1, raise_errors=False)

        assert exp.has_run
        assert exp.has_evaluated
        assert exp._datadog_experiment_id is not None
        assert isinstance(results, ExperimentResults)
        assert len(results) == len(synced_dataset)
        # Check structure of results (contains merged data)
        assert "input" in results[0]
        assert "output" in results[0]
        assert "evaluations" in results[0]
        assert "simple_match_evaluator" in results[0]["evaluations"]
        assert "length_evaluator" in results[0]["evaluations"]
        assert results[0]["evaluations"]["simple_match_evaluator"]["value"] is True

    def test_run_full_task_error_no_raise(self, synced_dataset, experiments_vcr):
        """Test full run() when task fails but raise_errors=False."""
        exp_name = f"test-run-full-task-err-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=failing_task, # Task fails
            dataset=synced_dataset,
            evaluators=[simple_match_evaluator]
        )

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_full_task_error.yaml"):
                results = exp.run(jobs=1, raise_errors=False)

        assert exp.has_run # Task ran (and failed)
        assert exp.has_evaluated # Evaluations ran on the (None) outputs
        assert isinstance(results, ExperimentResults)
        # Check task error captured in results
        assert results[0]["output"] is None
        assert results[0]["error"]["message"] == "Task failed intentionally"
        # Check evaluator ran on None output (should likely be False)
        assert "simple_match_evaluator" in results[0]["evaluations"]
        assert results[0]["evaluations"]["simple_match_evaluator"]["value"] is False # Comparing None != "expected"
        assert results[0]["evaluations"]["simple_match_evaluator"]["error"] is None

    def test_run_full_eval_error_no_raise(self, synced_dataset, experiments_vcr):
        """Test full run() when evaluator fails but raise_errors=False."""
        exp_name = f"test-run-full-eval-err-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(
            name=exp_name,
            task=simple_identity_task, # Task succeeds
            dataset=synced_dataset,
            evaluators=[failing_evaluator] # Evaluator fails
        )

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_full_eval_error.yaml"):
                results = exp.run(jobs=1, raise_errors=False)

        assert exp.has_run
        assert exp.has_evaluated
        assert isinstance(results, ExperimentResults)
        # Check task output is fine
        assert results[0]["output"] is not None
        assert results[0]["error"]["message"] is None
        # Check evaluator error is captured
        assert "failing_evaluator" in results[0]["evaluations"]
        assert results[0]["evaluations"]["failing_evaluator"]["value"] is None
        assert results[0]["evaluations"]["failing_evaluator"]["error"]["message"] == "Evaluator failed intentionally"



class TestExperimentPushSummaryMetric:
    """Tests for Experiment.push_summary_metric."""

    # Fixture to get an experiment that exists in DD
    @pytest.fixture
    def existing_experiment(self, experiment_after_task_run):
        # Re-use the fixture that already created an experiment
        assert experiment_after_task_run._datadog_experiment_id is not None
        return experiment_after_task_run

    def test_push_summary_metric_numeric(self, existing_experiment, experiments_vcr):
        """Push a numeric (score) summary metric."""
        metric_name = "overall_accuracy"
        metric_value = 0.85
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_push_summary_metric_numeric.yaml"):
                 existing_experiment.push_summary_metric(metric_name, metric_value, position=0)
        # VCR cassette should contain POST to /events with metric_type: score, score_value: 0.85

    def test_push_summary_metric_categorical(self, existing_experiment, experiments_vcr):
        """Push a categorical summary metric (string)."""
        metric_name = "pass_category"
        metric_value = "High"
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_push_summary_metric_categorical.yaml"):
                 existing_experiment.push_summary_metric(metric_name, metric_value, position=0)
        # VCR cassette should contain POST to /events with metric_type: categorical, categorical_value: "high"

    def test_push_summary_metric_boolean(self, existing_experiment, experiments_vcr):
        """Push a boolean summary metric (treated as categorical)."""
        metric_name = "meets_criteria"
        metric_value = True
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_push_summary_metric_boolean.yaml"):
                 existing_experiment.push_summary_metric(metric_name, metric_value, position=0)
        # VCR cassette should contain POST to /events with metric_type: categorical, categorical_value: "true"

    def test_push_summary_metric_before_create(self, simple_local_dataset):
        """Raise ValueError if push called before experiment exists in DD."""
        exp = dne.Experiment("local-exp", simple_identity_task, simple_local_dataset, [])
        assert exp._datadog_experiment_id is None # Not created yet
        with pytest.raises(ValueError, match="Experiment has not been created in Datadog"):
            exp.push_summary_metric("some_metric", 1.0)

    def test_push_summary_metric_invalid_type(self, existing_experiment):
        """Raise TypeError if value type is unsupported."""
        with pytest.raises(TypeError, match="Unsupported metric value type"):
            existing_experiment.push_summary_metric("invalid_metric", ["list", "is", "bad"])


class TestExperimentRepr:
    """Tests for the Experiment.__repr__ output."""

    # Helper to strip ANSI codes for easier assertions
    def _strip_ansi(self, text):
        import re
        ansi_escape = re.compile(r'\\x1B(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def test_repr_initial_local(self, simple_local_dataset):
        """Repr for a new, local-only experiment."""
        exp = dne.Experiment(
            name="repr-local", task=simple_identity_task, dataset=simple_local_dataset,
            evaluators=[simple_match_evaluator], tags=["repr"], description="Local Repr"
        )
        rep = repr(exp)
        rep_clean = self._strip_ansi(rep)

        # Check key components are present
        assert "Experiment(name=repr-local)" in rep_clean
        assert "Project: Test-Project-Experiment" in rep_clean
        assert "Description: Local Repr" in rep_clean
        assert f"Dataset: {simple_local_dataset.name}" in rep_clean
        assert f"({len(simple_local_dataset)} records)" in rep_clean
        assert "Evaluators: 1 evaluator" in rep_clean
        assert "(simple_match_evaluator)" in rep_clean
        assert "Status:" in rep_clean
        assert "Not run" in rep_clean
        assert "Pending run" in rep_clean
        assert "Local only" in rep_clean
        assert "Tags: #repr" in rep_clean
        assert "URL:" not in rep_clean # Should not have URL when local

    
    def test_repr_after_task_datadog(self, experiment_after_task_run):
        """Repr after run_task with Datadog sync."""
        exp = experiment_after_task_run # Already ran task and synced
        rep = repr(exp)
        rep_clean = self._strip_ansi(rep)

        # Check key components are present
        assert f"Experiment(name={exp.name})" in rep_clean # Name might have suffix
        assert "Status:" in rep_clean
        assert "✓ Run complete" in rep_clean
        assert "Not evaluated" in rep_clean
        assert "✓ Synced" in rep_clean
        assert "URL:" in rep_clean
        assert f"/llm/testing/experiments/{exp._datadog_experiment_id}" in rep_clean

    
    def test_repr_after_full_run_datadog(self, synced_dataset, experiments_vcr):
        """Repr after a full run() with Datadog sync."""
        exp_name = f"repr-full-run-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(name=exp_name, task=simple_identity_task, dataset=synced_dataset, evaluators=[simple_match_evaluator])
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_repr_full_run.yaml"):
                 exp.run(jobs=1) # Run fully
        rep = repr(exp)
        rep_clean = self._strip_ansi(rep)

        # Check key components are present
        assert f"Experiment(name={exp.name})" in rep_clean # Name might have suffix
        assert "Status:" in rep_clean
        assert "✓ Run complete" in rep_clean
        assert "✓ Evaluated" in rep_clean
        assert "✓ Synced" in rep_clean
        assert "URL:" in rep_clean
        assert f"/llm/testing/experiments/{exp._datadog_experiment_id}" in rep_clean

    
    def test_repr_after_task_with_errors(self, synced_dataset, experiments_vcr):
        """Repr shows error count after run_task with errors."""
        exp_name = f"repr-task-errors-{uuid.uuid4().hex[:6]}"
        exp = dne.Experiment(name=exp_name, task=failing_task, dataset=synced_dataset, evaluators=[])
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
             with experiments_vcr.use_cassette("test_experiment_run_task_error_no_raise.yaml"):
                 exp.run_task(jobs=1, raise_errors=False) # Run task that fails
        rep = repr(exp)
        rep_clean = self._strip_ansi(rep)

        # Note: Name assertion still checks prefix due to potential suffix
        assert exp.name.startswith(exp_name.split('-')[0])

        # Check key components including error info
        assert "Experiment(name=" in rep_clean
        assert "Status:" in rep_clean
        assert "✓ Run complete" in rep_clean
        error_count = len(synced_dataset)
        assert f"({error_count} errors)" in rep_clean # Check for error count
        assert ", 100.0%)" in rep_clean # Check for percentage
        assert "Not evaluated" in rep_clean
        assert "✓ Synced" in rep_clean
        assert "URL:" in rep_clean
        assert f"/llm/testing/experiments/{exp._datadog_experiment_id}" in rep_clean



@dne.summary_metric
def average_length_summary(outputs, evaluations):
    """Calculates average output length."""
    total_len = 0
    count = 0
    for out_item in outputs:
        output = out_item.get("output")
        if output is not None:
            try:
                total_len += len(str(output))
                count += 1
            except Exception: # Ignore errors converting to string/len
                pass
    return total_len / count if count > 0 else 0.0

@dne.summary_metric
def pass_fail_counts_summary(outputs, evaluations):
    """Counts passes and fails based on 'simple_match_evaluator'."""
    passes = 0
    fails = 0
    for eval_item in evaluations:
        match_eval = eval_item.get("evaluations", {}).get("simple_match_evaluator", {})
        if match_eval and match_eval.get("error") is None:
            if match_eval.get("value") is True:
                passes += 1
            else:
                fails += 1
    return {"passes": passes, "fails": fails}

@dne.summary_metric
def failing_summary(outputs, evaluations):
    """Summary metric that always fails."""
    raise ValueError("Summary metric failed intentionally")

@dne.summary_metric
def none_returning_summary(outputs, evaluations):
    """Summary metric that returns None."""
    return None


class TestExperimentSummaryMetrics:
    """Tests related to summary metric functionality."""

    @pytest.fixture
    def experiment_with_summaries(self, experiment_after_task_run):
        """Provides an experiment instance with summary metrics configured."""
        # Use the fixture that already ran the task
        exp = experiment_after_task_run
        # Add summary metrics
        exp.summary_metrics = [
            average_length_summary,
            pass_fail_counts_summary,
            failing_summary,
            none_returning_summary,
        ]
        # Ensure evaluators needed by summaries are present
        if simple_match_evaluator not in exp.evaluators:
             exp.evaluators.append(simple_match_evaluator)
        # Reset evaluation status so we can run it again
        exp.has_evaluated = False
        exp.evaluations = []
        exp._summary_metric_results = {}
        exp.results = None
        return exp

    def test_init_invalid_summary_metric_decorator(self, simple_local_dataset):
        """Raise TypeError if a summary metric is not decorated."""
        def undecorated_summary(outputs, evaluations): return 0.0
        with pytest.raises(TypeError, match="Summary metric 'undecorated_summary' must be decorated with @summary_metric"):
            dne.Experiment(
                "bad-summary", simple_identity_task, simple_local_dataset, [],
                summary_metrics=[average_length_summary, undecorated_summary]
            )

    @patch("ddtrace.llmobs.experimentation._experiment.Experiment.push_summary_metric")
    def test_run_summary_metrics_success_and_capture(self, mock_push, experiment_with_summaries, experiments_vcr):
        """Test summary metrics run, push is called, and results are stored."""
        exp = experiment_with_summaries
        # Mock environment for pushing
        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_summary_metrics.yaml"):
                 results = exp.run_evaluations() # This triggers _run_summary_metrics

        assert exp.has_evaluated
        assert isinstance(results, ExperimentResults)
        assert results.summary_metric_results is not None

        # Check stored results in Experiment and ExperimentResults
        stored_results = exp._summary_metric_results
        assert stored_results == results.summary_metric_results

        # 1. average_length_summary (single value)
        assert "average_length_summary" in stored_results
        assert stored_results["average_length_summary"]["error"] is None
        # Calculate expected average length (inputs are "What is 1+1?", "Capital of France?")
        expected_avg_len = (len("What is 1+1?") + len("Capital of France?")) / 2
        assert stored_results["average_length_summary"]["value"] == pytest.approx(expected_avg_len)
        mock_push.assert_any_call("average_length_summary", pytest.approx(expected_avg_len))

        # 2. pass_fail_counts_summary (dict value)
        # Check keys are prefixed with function name
        assert "pass_fail_counts_summary.passes" in stored_results
        assert "pass_fail_counts_summary.fails" in stored_results
        assert stored_results["pass_fail_counts_summary.passes"]["value"] == 2 # Both match expected output
        assert stored_results["pass_fail_counts_summary.passes"]["error"] is None
        assert stored_results["pass_fail_counts_summary.fails"]["value"] == 0
        assert stored_results["pass_fail_counts_summary.fails"]["error"] is None
        mock_push.assert_any_call("passes", 2)
        mock_push.assert_any_call("fails", 0)

        # 3. failing_summary (error)
        assert "failing_summary" in stored_results
        assert stored_results["failing_summary"]["value"] is None
        assert stored_results["failing_summary"]["error"] is not None
        assert stored_results["failing_summary"]["error"]["message"] == "Summary metric failed intentionally"
        assert stored_results["failing_summary"]["error"]["type"] == "ValueError"

        # 4. none_returning_summary (None value)
        assert "none_returning_summary" in stored_results
        assert stored_results["none_returning_summary"]["value"] is None
        assert stored_results["none_returning_summary"]["error"] is None

        # Check total calls to push (only for successful metrics)
        assert mock_push.call_count == 3 # avg_length, passes, fails

    @patch("ddtrace.llmobs.experimentation._experiment.Experiment.push_summary_metric")
    @patch("builtins.print") # Mock print to check local output
    def test_run_summary_metrics_local_mode(self, mock_print, mock_push, experiment_with_summaries, experiments_vcr):
        """Test summary metrics run locally (no push) when not synced."""
        exp = experiment_with_summaries
        # Simulate local run - remove DD ID
        exp._datadog_experiment_id = None

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=True):
            # Need cassette for _push_evals (which will fail here, but summary should still run)
            with experiments_vcr.use_cassette("test_experiment_run_summary_metrics_local.yaml"):
                with pytest.raises(ValueError, match="Experiment has not been created in Datadog"): # _push_evals fails
                    exp.run_evaluations() # This triggers _run_summary_metrics after push fails

        # Even though _push_evals failed, summary metrics should have run
        stored_results = exp._summary_metric_results
        assert "average_length_summary" in stored_results # Check one ran
        assert stored_results["average_length_summary"]["error"] is None
        expected_avg_len = (len("What is 1+1?") + len("Capital of France?")) / 2
        assert stored_results["average_length_summary"]["value"] == pytest.approx(expected_avg_len)

        # Check push was *not* called
        mock_push.assert_not_called()

        # Check print output for local metrics
        mock_print.assert_any_call(f"  {Color.YELLOW}Local summary metric 'average_length_summary' = {expected_avg_len:.1f} (not pushed){Color.RESET}")
        mock_print.assert_any_call(f"  {Color.YELLOW}Local summary metric 'passes' = 2 (not pushed){Color.RESET}")
        mock_print.assert_any_call(f"  {Color.YELLOW}Local summary metric 'fails' = 0 (not pushed){Color.RESET}")
        # Check error print for the failing one
        mock_print.assert_any_call(f"  {Color.RED}Error running summary metric function 'failing_summary': Summary metric failed intentionally{Color.RESET}")
        # Check print for the None one
        mock_print.assert_any_call(f"  {Color.DIM}Summary metric 'none_returning_summary' produced no results.{Color.RESET}")


    @patch("ddtrace.llmobs.experimentation._experiment.Experiment.push_summary_metric")
    def test_run_summary_metrics_push_error(self, mock_push, experiment_with_summaries, experiments_vcr):
        """Test capturing error when push_summary_metric itself fails."""
        exp = experiment_with_summaries
        # Make push fail for the first metric
        mock_push.side_effect = [ValueError("Push failed"), None, None] # Fail on first call only

        with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
            with experiments_vcr.use_cassette("test_experiment_run_summary_metrics_push_fail.yaml"):
                 results = exp.run_evaluations()

        stored_results = results.summary_metric_results
        # Check the first metric captured the push error
        assert "average_length_summary" in stored_results
        assert stored_results["average_length_summary"]["value"] is not None # Value was calculated
        assert stored_results["average_length_summary"]["error"] is not None
        assert stored_results["average_length_summary"]["error"]["message"] == "Push failed"
        assert stored_results["average_length_summary"]["error"]["type"] == "ValueError"

        # Check other metrics were processed and pushed (mock allows subsequent calls)
        assert "pass_fail_counts_summary.passes" in stored_results
        assert stored_results["pass_fail_counts_summary.passes"]["error"] is None
        assert "failing_summary" in stored_results # Still failed internally
        assert stored_results["failing_summary"]["error"]["type"] == "ValueError"

        assert mock_push.call_count == 3 # Push was attempted 3 times


    # --- Test ExperimentResults Integration ---

    # Most of the ExperimentResults integration is implicitly tested above,
    # but let's add a specific test for its __repr__.

    def test_experiment_results_repr_with_summaries(self, experiment_with_summaries, experiments_vcr):
        """Test ExperimentResults repr includes summary metrics."""
        # Run evaluations to populate results
        with patch("ddtrace.llmobs.experimentation._experiment.Experiment.push_summary_metric"): # Mock push
             with patch("ddtrace.llmobs.experimentation._experiment._is_locally_initialized", return_value=False):
                 with experiments_vcr.use_cassette("test_experiment_results_repr_summaries.yaml"):
                      results = experiment_with_summaries.run_evaluations()

        rep = repr(results)
        # Helper to strip ANSI codes for easier assertions
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        rep_clean = ansi_escape.sub('', rep)


        assert "ExperimentResults(" in rep_clean
        assert "Evaluations:" in rep_clean # Check evals are still there
        assert "Summary Metrics:" in rep_clean # Check new section

        # Check specific summary metric lines
        expected_avg_len = (len("What is 1+1?") + len("Capital of France?")) / 2
        assert f"average_length_summary: {expected_avg_len:.1f}" in rep_clean
        assert "pass_fail_counts_summary.passes: 2" in rep_clean
        assert "pass_fail_counts_summary.fails: 0" in rep_clean
        assert "failing_summary: Error (ValueError: Summary metric failed intentionally)" in rep_clean
        assert "none_returning_summary: No value returned" in rep_clean
        assert f"URL: {get_base_url()}/llm/testing/experiments/{experiment_with_summaries._datadog_experiment_id}" in rep_clean
