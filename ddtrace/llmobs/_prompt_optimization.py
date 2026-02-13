"""Prompt optimization framework for iteratively improving LLM prompts."""

from dataclasses import dataclass
import random
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional
from typing import Sequence
from typing import TypedDict

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import DatasetRecordInputType
from ddtrace.llmobs._experiment import EvaluatorType
from ddtrace.llmobs._experiment import Experiment
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRowResult
from ddtrace.llmobs._experiment import JSONType
from ddtrace.llmobs._experiment import SummaryEvaluatorType


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs


log = get_logger(__name__)

TIPS = {
    "creative": "Don't be afraid to be creative when creating the new instruction!",
    "simple": "Keep the instruction clear and concise.",
    "description": "Make sure your instruction is very informative and descriptive.",
    "specific": "The instruction should include specific details such as numbers or conditions.",
    "specificity": (
        "Be more specific in your instructions. Instead of 'handle errors', "
        "specify exactly what types of errors and how to handle them."
    ),
    "examples": "Add concrete examples to illustrate the expected format and behavior.",
    "structure": ("Use clear structure with numbered steps or bullet points to organize complex instructions."),
    "constraints": "Add explicit constraints and validation rules to prevent common mistakes.",
    "context": ("Provide more context about the domain and use case to help the model understand the task better."),
    "edge_cases": "Address edge cases and corner scenarios that might cause failures.",
    "formatting": ("Be explicit about output formatting requirements, including JSON structure and data types."),
    "clarity": "Use simpler, clearer language to reduce ambiguity in instructions.",
    "validation": "Include validation steps or self-checking mechanisms in the prompt.",
    "priorities": "Clearly state what aspects are most important when there are trade-offs.",
}

_DATASET_SPLIT_SEED = 42  # Fixed seed for reproducible dataset shuffling


class IterationData(TypedDict, total=False):
    """Data for a single optimization iteration."""

    iteration: int  # required
    prompt: str  # required
    results: ExperimentResult  # required
    score: float  # required
    experiment_url: str  # required
    summary_evaluations: dict[str, dict[str, JSONType]]  # required
    train_experiment_url: str  # optional, present when dataset splitting is enabled


@dataclass
class TestPhaseResult:
    """Results from the final test phase of prompt optimization (when dataset splitting is enabled)."""

    results: ExperimentResult
    score: float
    experiment_url: str


class OptimizationIteration:
    """Represents a single iteration in the prompt optimization process.

    Each iteration analyzes the current prompt's performance and suggests improvements.
    """

    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: ExperimentResult,
        optimization_task: Callable,
        config: ConfigType,
        labelization_function: Optional[Callable[[dict[str, Any]], str]],
    ) -> None:
        """Initialize an optimization iteration.

        :param iteration: The iteration number (0-indexed).
        :param current_prompt: The current prompt being evaluated.
        :param current_results: Results from the previous experiment run.
        :param optimization_task: Function to generate prompt improvements.
        :param config: Configuration for the optimization task.
        :param labelization_function: Function to generate labels from individual results.
                                     Takes an individual result dict and returns a string label.
        """
        self.iteration = iteration
        self.current_prompt = current_prompt
        self.current_results = current_results
        self._optimization_task = optimization_task
        self._config = config
        self._labelization_function = labelization_function

    def run(self) -> str:
        """Run the optimization task to generate an improved prompt.

                Follows the LLM-as-a-judge pattern:
                1. Loads the optimization prompt template from _prompt_optimization.md
                2. Builds user prompt with examples from evaluation results
        ``
                3. Calls optimization_task (LLM) with system and user prompts
                4. Returns improved prompt

                :return: The improved prompt string.
        """
        # Step 1: Load and prepare system prompt template
        system_prompt = self._load_system_prompt()

        # Step 2: Build user prompt with current prompt and examples
        user_prompt = self._build_user_prompt()

        # Step 3
        try:
            improved_prompt = self._optimization_task(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=self._config,
            )
        except Exception as e:
            log.error(
                "Iteration %s: Failed to run optimization_task",
                self.iteration,
            )
            log.error("Exception type: %s", type(e).__name__)
            log.error("Exception message: %s", str(e))
            improved_prompt = ""

        if not improved_prompt:
            log.warning(
                "Iteration %s: optimization_task returned empty 'new_prompt', keeping current prompt",
                self.iteration,
            )
            return self.current_prompt

        # Step 4: Return improved prompt
        return improved_prompt

    def _load_system_prompt(self) -> str:
        """Load and prepare the optimization system prompt.

        Loads the template from _prompt_optimization.md and replaces placeholders.
        Adds evaluation model information and random tip at the end.

        :return: System prompt string with output format injected.
        """
        import os
        import random

        # Get the directory of this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(current_dir, "_prompt_optimization.md")

        # Load template
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        output_format = self._config.get("evaluation_output_format")
        structure_placeholder = ""
        if output_format:
            structure_placeholder = "\n".join(
                [
                    "## Prompt Output Format Requirements",
                    "The optimized prompt must guide the LLM to produce JSON output with this structure:",
                    "\n",
                    str(output_format),
                    "\n",
                    "**If this output format is not clearly specified in the initial prompt**",
                    "**add it as your first improvement step**",
                ]
            )

        system_prompt = template.replace("{{STRUCTURE_PLACEHOLDER}}", structure_placeholder)

        # Add evaluation model information at the end
        additional_parts = []
        if "model_name" in self._config:
            eval_model = self._config["model_name"]
            additional_parts.append(
                f"\n\nIMPORTANT: The improved prompt will be applied to this evaluation model: {eval_model}"
            )
            additional_parts.append(
                "Consider the capabilities, limitations, and characteristics of this specific model "
                "when optimizing the prompt."
            )

        # Add random tip at the end
        tip_key = random.choice(list(TIPS.keys()))  # nosec B311
        tip_text = TIPS[tip_key]
        additional_parts.append(f"\n\n**TIP: {tip_text}**")

        if additional_parts:
            system_prompt += "\n".join(additional_parts)

        return system_prompt

    def _add_examples(self, individual_results: list[ExperimentRowResult]) -> str:
        """Add examples of each label type using the labelization function.

        Applies the labelization function to each individual result to generate labels,
        then selects one random example for each unique label.

        :param individual_results: list of experiment result dicts.
        :return: Formatted string with examples, or empty string if no examples found.
        """
        if not individual_results or self._labelization_function is None:
            return ""

        # Step 1: Apply labelization function to each result and collect by label
        examples_by_label: dict[str, list[ExperimentRowResult]] = {}
        for result in individual_results:
            # Cast ExperimentRowResult to dict[str, Any] for labelization function
            label = self._labelization_function(dict(result))
            if label:  # Only add if label is not None or empty
                if label not in examples_by_label:
                    examples_by_label[label] = []
                examples_by_label[label].append(result)

        if not examples_by_label:
            return ""

        if len(examples_by_label) > 10:
            log.warning("Too many distinct labels: %s", len(examples_by_label))
            return ""

        # Step 2: Randomly select one example for each label
        examples = {}
        for label, label_examples in examples_by_label.items():
            if label_examples:
                examples[label] = random.choice(label_examples)  # nosec B311

        if not examples:
            return ""

        # Step 3: Format examples with proper headers
        formatted_parts = ["## Examples from Current Evaluation\n"]

        for label, example in sorted(examples.items(), key=lambda x: str(x[0])):
            formatted_parts.append(f"### {label}\n")
            formatted_parts.append(self._format_example(example))
            formatted_parts.append("")  # Add spacing between examples

        return "\n".join(formatted_parts)

    def _format_example(self, example: ExperimentRowResult) -> str:
        """Format an example for display in the user prompt.

        :param example: Example result dict.
        :return: Formatted string.
        """
        parts = []

        # Input
        input_data = example["input"]
        parts.append(f"Input:\n{input_data}")

        # Expected output
        expected = example["expected_output"]
        if expected:
            parts.append(f"Expected Output:\n{expected}")

        # Actual output
        output = example["output"]
        if output:
            parts.append(f"Actual Output:\n{output}")

        # Evaluation reasoning if available
        evaluations = example["evaluations"]
        if evaluations:
            for eval_name, eval_data in evaluations.items():
                if isinstance(eval_data, dict) and "reasoning" in eval_data:
                    parts.append(f"Reasoning ({eval_name}):\n{eval_data['reasoning']}")

        return "\n".join(parts)

    def _build_user_prompt(self) -> str:
        """Build user prompt with current prompt and evaluation examples.

        Includes:
        - Current prompt being optimized
        - Performance metrics
        - Examples from results (TP, TN, FP, FN if available)

        :return: User prompt string.
        """
        prompt_parts = [f"Initial Prompt:\n{self.current_prompt}\n"]

        # Extract examples from evaluation results
        results = self.current_results
        summary_evals = results.get("summary_evaluations")

        # Add performance metrics if available
        if summary_evals:
            prompt_parts.append("Performance Metrics:")
            for _, summary_metric_data in summary_evals.items():
                value = summary_metric_data.get("value", {})
                if isinstance(value, dict):
                    for metric_name, metric_data in value.items():
                        prompt_parts.append(f"- {metric_name}: {metric_data}")
            prompt_parts.append("")

        # Get individual results to find examples
        individual_results = results.get("rows", [])

        if individual_results:
            prompt_parts.append(self._add_examples(individual_results))

        final_prompt = "\n\n".join(prompt_parts)
        return final_prompt


class OptimizationResult:
    """Results from running a prompt optimization.

    Contains all iteration results and metadata about the optimization process.

    Example usage::

        result = optimization.run()

        # Access best iteration
        print(f"Best prompt: {result.best_prompt}")
        print(f"Best score: {result.best_score}")
        print(f"Best iteration: {result.best_iteration}")

        # Access full history
        for iteration in result.get_history():
            print(f"Iteration {iteration['iteration']}: {iteration['score']}")

    """

    def __init__(
        self,
        name: str,
        initial_prompt: str,
        iterations: list[IterationData],
        best_iteration: int,
        test_phase: Optional[TestPhaseResult] = None,
    ) -> None:
        """Initialize optimization results.

        :param name: Name of the optimization run.
        :param initial_prompt: The starting prompt.
        :param iterations: list of results from each iteration (IterationData).
        :param best_iteration: Index of the iteration with best performance.
        :param test_phase: Results from the final test phase (when dataset splitting is enabled).
        """
        self.name = name
        self.initial_prompt = initial_prompt
        self.iterations = iterations
        self.best_iteration = best_iteration
        self._test_phase = test_phase

    @property
    def best_prompt(self) -> str:
        """Get the best performing prompt from all iterations."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return self.initial_prompt
        return self.iterations[self.best_iteration]["prompt"]

    @property
    def best_score(self) -> Optional[float]:
        """Get the evaluation score of the best iteration."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return None
        return self.iterations[self.best_iteration]["score"]

    @property
    def best_experiment_url(self) -> Optional[str]:
        """Get the experiment URL for the best iteration."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return None
        return self.iterations[self.best_iteration]["experiment_url"]

    @property
    def total_iterations(self) -> int:
        """Get the total number of iterations run (including baseline)."""
        return len(self.iterations)

    @property
    def test_score(self) -> Optional[float]:
        """Get the score from the final test experiment (when dataset splitting is enabled)."""
        return self._test_phase.score if self._test_phase else None

    @property
    def test_experiment_url(self) -> Optional[str]:
        """Get the experiment URL for the final test experiment."""
        return self._test_phase.experiment_url if self._test_phase else None

    @property
    def test_results(self) -> Optional[ExperimentResult]:
        """Get the results from the final test experiment."""
        return self._test_phase.results if self._test_phase else None

    def get_history(self) -> list[IterationData]:
        """Get the full optimization history with all iterations.

        Returns a list of IterationData dicts, one per iteration.

        :return: list of iteration results.
        """
        return self.iterations

    def get_score_history(self) -> list[float]:
        """Get list of scores across all iterations.

        :return: list of scores in iteration order.
        """
        return [it["score"] for it in self.iterations]

    def get_prompt_history(self) -> list[str]:
        """Get list of prompts across all iterations.

        :return: list of prompts in iteration order.
        """
        return [it["prompt"] for it in self.iterations]

    def summary(self) -> str:
        """Get a human-readable summary of the optimization results.

        :return: Formatted summary string.
        """
        lines = [
            f"Optimization: {self.name}",
            f"Total iterations: {self.total_iterations}",
            f"Best iteration: {self.best_iteration}",
            f"Best score: {self.best_score:.4f}" if self.best_score is not None else "Best score: N/A",
        ]

        if self.test_score is not None:
            lines.append(f"Test score: {self.test_score:.4f}")
            if self.test_experiment_url:
                lines.append(f"Test experiment: {self.test_experiment_url}")

        # Add best iteration's summary evaluations
        for iteration in self.iterations:
            if iteration["iteration"] == self.best_iteration:
                summary_evals = iteration["summary_evaluations"]
                if summary_evals:
                    lines.append(f"\nBest iteration summary evaluations:\n{summary_evals}")
                break

        # Add test set summary evaluations
        if self.test_results is not None:
            test_summary_evals = self.test_results.get("summary_evaluations", {})
            if test_summary_evals:
                lines.append(f"\nTest set summary evaluations:\n{test_summary_evals}")

        lines.append("\nScore progression:")
        for iteration in self.iterations:
            iter_num = iteration["iteration"]
            score = iteration["score"]
            url = iteration["experiment_url"]
            marker = " <- BEST" if iter_num == self.best_iteration else ""
            lines.append(f"Iteration {iter_num} (score: {score:.4f}): {url}{marker}")

        return "\n".join(lines)


class PromptOptimization:
    """Iteratively optimize LLM prompts using experiments and evaluations.

    PromptOptimization runs a baseline experiment with an initial prompt, then iteratively
    improves the prompt based on evaluation results. Each iteration analyzes performance
    and generates improved prompt suggestions.
    """

    def __init__(
        self,
        name: str,
        task: Callable[[DatasetRecordInputType, Optional[ConfigType]], JSONType],
        optimization_task: Callable[[str, str, ConfigType], str],
        dataset: Dataset,
        evaluators: Sequence[EvaluatorType],
        project_name: str,
        config: ConfigType,
        summary_evaluators: Sequence[SummaryEvaluatorType],
        compute_score: Callable[[dict[str, dict[str, Any]]], float],
        labelization_function: Optional[Callable[[dict[str, Any]], str]],
        _llmobs_instance: Optional["LLMObs"] = None,
        tags: Optional[dict[str, str]] = None,
        max_iterations: int = 5,
        stopping_condition: Optional[Callable[[dict[str, dict[str, Any]]], bool]] = None,
        dataset_split: Union[bool, tuple[float, ...]] = False,
        test_dataset: Optional[Dataset] = None,
    ) -> None:
        """Initialize a prompt optimization.

        :param name: Name of the optimization run.
        :param task: Task function to execute. Must accept ``input_data`` and ``config`` parameters.
        :param optimization_task: Function to generate prompt improvements. Must accept
                                  ``system_prompt`` (str), ``user_prompt`` (str), and ``config`` (dict).
                                  Must return the new prompt.
        :param dataset: Dataset to run experiments on.
        :param evaluators: list of evaluators to measure task performance. Can be either
                          class-based evaluators (inheriting from BaseEvaluator) or function-based
                          evaluators that accept (input_data, output_data, expected_output) parameters.
        :param project_name: Project name for organizing optimization runs.
        :param config: Configuration dictionary. Must contain:
                      - ``prompt`` (mandatory): Initial prompt template
                      - ``model_name`` (optional): Model to use for task execution
                      - ``evaluation_output_format`` (optional): the output format wanted
                      - ``runs`` (optional): The number of times to run the experiment, or, run the task for every
                                             dataset record the defined number of times.
        :param summary_evaluators: list of summary evaluators (REQUIRED). Can be either
                                   class-based evaluators (inheriting from BaseSummaryEvaluator) or function-based
                                   evaluators that accept (inputs: list, outputs: list, expected_outputs: list,
                                   evaluations: dict) and return aggregated metrics.
        :param compute_score: Function to compute iteration score (REQUIRED).
                             Takes summary_evaluations dict from the experiment result and returns float score.
                             Used to compare and rank different prompt iterations.
        :param labelization_function: Function to generate labels from individual results (Optional but highly valuable)
                                     Takes an individual result dict (with "evaluations" key) and returns a string label
                                     Used to categorize examples shown to the optimization LLM.
                                     Example: lambda r: "Very good" if r["evaluations"]["score"] >= 0.8 else "Bad"
        :param _llmobs_instance: Internal LLMObs instance.
        :param tags: Optional tags to associate with the optimization.
        :param max_iterations: Maximum number of optimization iterations to run.
        :param stopping_condition: Optional function to determine when to stop optimization.
                                   Takes summary_evaluations dict from the experiment result
                                   and returns True if should stop.
        :param dataset_split: Controls dataset splitting. Accepts:
            - ``False`` (default): No splitting, use full dataset for everything.
            - ``True``: Split with default ratios (60/20/20 without test_dataset, 80/20 with).
            - ``(train, valid, test)`` tuple: Custom 3-way split ratios. Must sum to 1.0.
              Cannot be combined with ``test_dataset``.
            - ``(train, valid)`` tuple: Custom 2-way split ratios. Must sum to 1.0.
              Requires ``test_dataset`` for the test set.
        :param test_dataset: Optional separate test dataset. When provided, the main dataset is split
                            into train/valid (80/20) and this dataset is used for the final test.
                            Implicitly enables dataset splitting.
        :raises ValueError: If required config parameters or compute_score are missing.
        """
        self.name = name
        self._task = task
        self._optimization_task = optimization_task
        self._dataset = dataset
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators
        self._stopping_condition = stopping_condition
        self._labelization_function = labelization_function
        self._compute_score = compute_score
        self._tags: dict[str, str] = tags or {}
        self._tags["project_name"] = project_name
        self._llmobs_instance = _llmobs_instance
        self._max_iterations = max_iterations

        # Validate required config parameters
        if not config:
            raise ValueError("config parameter is required")

        if not isinstance(config, dict) or "prompt" not in config:
            raise ValueError("config must contain a 'prompt' key")

        self._initial_prompt = config["prompt"]
        self._model_name = config.get("model_name")
        self._config = config

        # Dataset splitting
        self._test_dataset = test_dataset
        self._dataset_split_enabled = bool(dataset_split) or test_dataset is not None

        self._split_ratios: Optional[tuple[float, ...]] = None
        if isinstance(dataset_split, tuple):
            self._split_ratios = dataset_split
        elif test_dataset is not None:
            self._split_ratios = (0.8, 0.2)  # Default 2-way when test_dataset provided
        elif dataset_split:
            self._split_ratios = (0.6, 0.2, 0.2)  # Default 3-way
        else:
            self._split_ratios = None  # No split

    def _make_sub_dataset(self, split_name: str, records: list[DatasetRecord]) -> Dataset:
        """Create a sub-dataset from a list of records.

        Follows the same pattern as Experiment._run_task for creating subset datasets.

        :param split_name: Name suffix for the sub-dataset (e.g. "train", "valid", "test").
        :param records: list of DatasetRecord dicts to include.
        :return: A new Dataset instance with the specified records.
        """
        from copy import deepcopy

        return Dataset(
            name=f"[{split_name}] {self._dataset.name}",
            project=self._dataset.project,
            dataset_id=self._dataset._id,
            records=[deepcopy(r) for r in records],
            description=self._dataset.description,
            latest_version=self._dataset._latest_version,
            version=self._dataset._version,
            _dne_client=self._dataset._dne_client,
        )

    def _create_split_datasets(self) -> tuple:
        """Split the dataset into train, valid, and test subsets.

        When ``test_dataset`` was provided, the main dataset is split into train/valid (80/20)
        and the external test dataset is used as-is.  Otherwise, the main dataset is split
        three ways (60/20/20).

        Records are shuffled with a fixed seed for reproducibility.

        :return: tuple of (train_dataset, valid_dataset, test_dataset).
        :raises ValueError: If any split would be empty.
        """
        if self._split_ratios is None:
            raise ValueError("_split_dataset called without split ratios")
        records = list(self._dataset)

        # Shuffle with fixed seed for reproducibility
        rng = random.Random(_DATASET_SPLIT_SEED)  # nosec B311
        rng.shuffle(records)

        if self._test_dataset is not None:
            # 2-way split: train/valid from main dataset, external test
            train_ratio = self._split_ratios[0]
            split_idx = int(train_ratio * len(records))
            train_records = records[:split_idx]
            valid_records = records[split_idx:]
            test_ds = self._test_dataset
        else:
            # 3-way split from main dataset
            n = len(records)
            train_ratio, valid_ratio = self._split_ratios[0], self._split_ratios[1]
            train_end = int(train_ratio * n)
            valid_end = int((train_ratio + valid_ratio) * n)
            train_records = records[:train_end]
            valid_records = records[train_end:valid_end]
            test_records = records[valid_end:]
            test_ds = self._make_sub_dataset("test", test_records)

        train_ds = self._make_sub_dataset("train", train_records)
        valid_ds = self._make_sub_dataset("valid", valid_records)

        # Validate no empty splits
        for split_name, ds in [("train", train_ds), ("valid", valid_ds), ("test", test_ds)]:
            if len(ds) == 0:
                raise ValueError(
                    f"Dataset split '{split_name}' is empty. "
                    f"Dataset has {len(self._dataset)} records, which is too few for splitting."
                )

        log.info("Dataset split: %d train, %d valid, %d test records", len(train_ds), len(valid_ds), len(test_ds))
        return train_ds, valid_ds, test_ds

    def run(
        self,
        jobs: int = 1,
    ) -> OptimizationResult:
        """Run the prompt optimization process.

        Executes a baseline experiment with the initial prompt, then iteratively
        improves the prompt based on evaluation results.

        When dataset splitting is enabled, train examples are used for the optimization LLM,
        valid scores rank iterations, and a final test experiment provides an unbiased score.

        :param jobs: Number of parallel jobs for experiment execution.
        :return: OptimizationResult containing all iteration results.
        :raises ValueError: If LLMObs is not enabled or dataset is too small for splitting.
        """
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the optimization via `LLMObs.prompt_optimization(...)` before running."
            )

        log.info("Starting prompt optimization: %s", self.name)

        if self._dataset_split_enabled:
            return self._run_with_split(jobs)
        return self._run_without_split(jobs)

    def _run_without_split(self, jobs: int) -> OptimizationResult:
        """Run optimization without dataset splitting (original behavior).

        :param jobs: Number of parallel jobs for experiment execution.
        :return: OptimizationResult containing all iteration results.
        """
        # Track all iteration results
        all_iterations: list[IterationData] = []
        best_iteration = 0
        best_score = None
        best_prompt = None
        best_results = None

        # Run baseline experiment with initial prompt
        iteration = 0
        current_prompt = str(self._initial_prompt)
        current_results, experiment_url = self._run_experiment(iteration, current_prompt, jobs)

        # Store baseline results
        summary_evals = current_results.get("summary_evaluations", {})
        baseline_score = self._compute_score(summary_evals)

        iteration_data: IterationData = {
            "iteration": iteration,
            "prompt": current_prompt,
            "results": current_results,
            "score": baseline_score,
            "experiment_url": experiment_url,
            "summary_evaluations": summary_evals,
        }
        all_iterations.append(iteration_data)
        best_score = baseline_score or 0.0
        best_prompt = current_prompt
        best_results = current_results

        log.info("Baseline score: %.3f", best_score)

        # Run optimization iterations
        for i in range(1, self._max_iterations + 1):
            # Always optimize from the best prompt so far
            optimization_iteration = OptimizationIteration(
                iteration=i,
                current_prompt=best_prompt,
                current_results=best_results,
                optimization_task=self._optimization_task,
                config=self._config,
                labelization_function=self._labelization_function,
            )

            # Generate improved prompt
            new_prompt = optimization_iteration.run()

            # Run experiment with improved prompt
            new_results, experiment_url = self._run_experiment(i, new_prompt, jobs)

            # Compute score for this iteration
            summary_evals = new_results.get("summary_evaluations", {})
            new_score = self._compute_score(summary_evals)

            # Track iteration results
            iteration_data = {
                "iteration": i,
                "prompt": new_prompt,
                "results": new_results,
                "score": new_score,
                "experiment_url": experiment_url,
                "summary_evaluations": summary_evals,
            }
            all_iterations.append(iteration_data)

            log.info("Iteration %s", i)
            log.info("%s", summary_evals)

            # Update best iteration if score improved
            if new_score is not None and (best_score is None or new_score > best_score):
                best_iteration = i
                best_score = new_score
                best_prompt = new_prompt
                best_results = new_results

            # Check stopping condition
            if self._stopping_condition and self._stopping_condition(summary_evals):
                log.info("Stopping condition met after iteration %s", i)
                break

        # Create result object with full history
        return OptimizationResult(
            name=self.name,
            initial_prompt=str(self._initial_prompt),
            iterations=all_iterations,
            best_iteration=best_iteration,
        )

    def _run_with_split(self, jobs: int) -> OptimizationResult:
        """Run optimization with train/valid/test dataset splitting.

        Train examples are shown to the optimization LLM, valid scores rank iterations,
        and a final test experiment on the best prompt provides an unbiased score.

        :param jobs: Number of parallel jobs for experiment execution.
        :return: OptimizationResult containing all iteration results and test results.
        """
        train_ds, valid_ds, test_ds = self._create_split_datasets()

        # Track all iteration results
        all_iterations: list[IterationData] = []
        best_iteration = 0
        best_score = None
        best_prompt = None
        best_train_results = None

        # Run baseline experiments (iteration 0)
        iteration = 0
        current_prompt = str(self._initial_prompt)

        train_results, train_url = self._run_experiment(
            iteration, current_prompt, jobs, dataset=train_ds, suffix="train"
        )
        valid_results, valid_url = self._run_experiment(
            iteration, current_prompt, jobs, dataset=valid_ds, suffix="valid"
        )

        # Score from valid set
        summary_evals = valid_results.get("summary_evaluations", {})
        baseline_score = self._compute_score(summary_evals)

        iteration_data: IterationData = {
            "iteration": iteration,
            "prompt": current_prompt,
            "results": valid_results,
            "score": baseline_score,
            "experiment_url": valid_url,
            "summary_evaluations": summary_evals,
            "train_experiment_url": train_url,
        }
        all_iterations.append(iteration_data)
        best_score = baseline_score or 0.0
        best_prompt = current_prompt
        best_train_results = train_results

        log.info("Baseline score (valid): %.3f", best_score)

        # Run optimization iterations
        for i in range(1, self._max_iterations + 1):
            # Optimize from the best prompt so far, using train results for examples
            optimization_iteration = OptimizationIteration(
                iteration=i,
                current_prompt=best_prompt,
                current_results=best_train_results,
                optimization_task=self._optimization_task,
                config=self._config,
                labelization_function=self._labelization_function,
            )

            # Generate improved prompt
            new_prompt = optimization_iteration.run()

            # Run experiments on train and valid sets
            train_results, train_url = self._run_experiment(i, new_prompt, jobs, dataset=train_ds, suffix="train")
            valid_results, valid_url = self._run_experiment(i, new_prompt, jobs, dataset=valid_ds, suffix="valid")

            # Compute score from valid set
            summary_evals = valid_results.get("summary_evaluations", {})
            new_score = self._compute_score(summary_evals)

            # Track iteration results
            iteration_data = {
                "iteration": i,
                "prompt": new_prompt,
                "results": valid_results,
                "score": new_score,
                "experiment_url": valid_url,
                "summary_evaluations": summary_evals,
                "train_experiment_url": train_url,
            }
            all_iterations.append(iteration_data)

            log.info("Iteration %s (valid score: %.3f)", i, new_score)
            log.info("%s", summary_evals)

            # Update best iteration if score improved
            if new_score is not None and (best_score is None or new_score > best_score):
                best_iteration = i
                best_score = new_score
                best_prompt = new_prompt
                best_train_results = train_results

            # Check stopping condition
            if self._stopping_condition and self._stopping_condition(summary_evals):
                log.info("Stopping condition met after iteration %s", i)
                break

        # Run final test experiment with the best prompt
        log.info("Running final test experiment with best prompt (iteration %s)", best_iteration)
        test_results, test_url = self._run_experiment(best_iteration, best_prompt, jobs, dataset=test_ds, suffix="test")
        test_summary_evals = test_results.get("summary_evaluations", {})
        test_score = self._compute_score(test_summary_evals)
        log.info("Test score: %.3f", test_score)

        return OptimizationResult(
            name=self.name,
            initial_prompt=str(self._initial_prompt),
            iterations=all_iterations,
            best_iteration=best_iteration,
            test_phase=TestPhaseResult(results=test_results, score=test_score, experiment_url=test_url),
        )

    def _run_experiment(
        self,
        iteration: int,
        prompt: str,
        jobs: int,
        dataset: Optional[Dataset] = None,
        suffix: str = "",
    ) -> tuple[ExperimentResult, str]:
        """Run an experiment for a given iteration and prompt.

        :param iteration: The iteration number.
        :param prompt: The prompt to test.
        :param jobs: Number of parallel jobs.
        :param dataset: Optional dataset override. If not provided, uses ``self._dataset``.
        :param suffix: Optional suffix appended to the experiment name (e.g. "_train", "_valid").
        :return: tuple of (experiment results dictionary, experiment URL).
        """
        ds = dataset or self._dataset
        iteration_name = "baseline" if iteration == 0 else f"iteration_{iteration}"
        if suffix:
            iteration_name = f"{iteration_name}_{suffix}"

        # Update config with current prompt
        # Start with base config, then override prompt with the new one
        config_updates = {"model_name": self._model_name, "prompt": prompt}
        experiment_config = self._config | config_updates

        # Get runs value and ensure it's int or None
        runs_value = self._config.get("runs")
        runs_int: Optional[int] = None
        if runs_value is not None and isinstance(runs_value, int):
            runs_int = runs_value

        experiment = Experiment(
            name=f"{self.name}_{iteration_name}",
            project_name=self._tags["project_name"],
            dataset=ds,
            task=self._task,
            evaluators=self._evaluators,
            summary_evaluators=self._summary_evaluators,
            _llmobs_instance=self._llmobs_instance,
            config=experiment_config,
            runs=runs_int,
        )

        experiment_results = experiment.run(
            raise_errors=True,
            jobs=jobs,
        )

        return experiment_results, experiment.url
