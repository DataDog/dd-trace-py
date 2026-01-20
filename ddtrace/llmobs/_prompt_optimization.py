"""Prompt optimization framework for iteratively improving LLM prompts."""

from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import Experiment

from ddtrace.llmobs._experiment import JSONType
from ddtrace.llmobs._experiment import NonNoneJSONType
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import DatasetRecordInputType

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


class OptimizationIteration:
    """Represents a single iteration in the prompt optimization process.

    Each iteration analyzes the current prompt's performance and suggests improvements.
    """

    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: Dict[str, Any],
        optimization_task: Callable,
        config: ConfigType,
    ) -> None:
        """Initialize an optimization iteration.

        :param iteration: The iteration number (0-indexed).
        :param current_prompt: The current prompt being evaluated.
        :param current_results: Results from the previous experiment run.
        :param optimization_task: Function to generate prompt improvements.
        :param config: Configuration for the optimization task.
        """
        self.iteration = iteration
        self.current_prompt = current_prompt
        self.current_results = current_results
        self._optimization_task = optimization_task
        self._config = config

    def run(self) -> str:
        """Run the optimization task to generate an improved prompt.

        Follows the LLM-as-a-judge pattern:
        1. Loads the optimization prompt template from _prompt_optimization.md
        2. Builds user prompt with examples (TP, TN, FP, FN) from evaluation results
        3. Calls optimization_task (LLM) with system and user prompts
        4. Parses structured output
        5. Returns improved prompt

        :return: The improved prompt string.
        """
        # Step 1: Load and prepare system prompt template
        system_prompt = self._load_system_prompt()

        # Step 2: Build user prompt with current prompt and examples
        user_prompt = self._build_user_prompt()

        # Step 3 & 4: Call optimization LLM
        try:
            result = self._optimization_task(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=self._config,
            )
        except Exception as e:
            log.error(
                "Iteration %d: Failed to run optimization_task with model '%s'",
                self.iteration,
                self._config.get("optimization_model_name", "unknown"),
            )
            log.error("Exception type: %s", type(e).__name__)
            log.error("Exception type: %s", str(e))
            result = {}

        # Parse the result dict
        improved_prompt = result.get("prompt")

        if not improved_prompt:
            log.warning(
                "Iteration %d: optimization_task returned empty 'new_prompt', keeping current prompt",
                self.iteration,
            )
            return self.current_prompt

        # Step 5: Return improved prompt
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

        # Replace {{EVALUATION_OUTPUT_FORMAT}} placeholder
        output_format = self._config.get("evaluation_output_format")
        system_prompt = template.replace("{{EVALUATION_OUTPUT_FORMAT}}", output_format)

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
        tip_key = random.choice(list(TIPS.keys()))
        tip_text = TIPS[tip_key]
        additional_parts.append(f"\n\n**TIP: {tip_text}**")

        if additional_parts:
            system_prompt += "\n".join(additional_parts)

        return system_prompt

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
                for metric_name, metric_data in summary_metric_data.get("value", {}).items():
                    prompt_parts.append(f"- {metric_name}: {metric_data}")
            prompt_parts.append("")

        # Get individual results to find examples
        individual_results = results.get("rows", [])

        if individual_results:
            # Find examples of each type
            fp_example = self._find_example(individual_results, "false_positive")
            fn_example = self._find_example(individual_results, "false_negative")
            tn_example = self._find_example(individual_results, "true_negative")
            tp_example = self._find_example(individual_results, "true_positive")

            # Add BAD examples first (FP, FN)
            if fp_example:
                prompt_parts.append("BAD EXAMPLE (False Positive):")
                prompt_parts.append(self._format_example(fp_example))

            if fn_example:
                prompt_parts.append("BAD EXAMPLE (False Negative):")
                prompt_parts.append(self._format_example(fn_example))

            # Add GOOD examples (TN, TP)
            if tn_example:
                prompt_parts.append("GOOD EXAMPLE (True Negative):")
                prompt_parts.append(self._format_example(tn_example))

            if tp_example:
                prompt_parts.append("GOOD EXAMPLE (True Positive):")
                prompt_parts.append(self._format_example(tp_example))

        return "\n\n".join(prompt_parts)

    def _find_example(self, results: List[Dict[str, Any]], example_type: str) -> Optional[Dict[str, Any]]:
        """Find an example of specified type from results.

        The evaluators in evaluations are like this:
        {'evaluator_function_name': {'value': 'returned_value', 'error': None}}

        :param results: List of experiment results.
        :param example_type: Type to find (false_positive, false_negative, true_positive, true_negative).
        :return: Example dict or None.
        """
        for result in results:
            evaluations = result.get("evaluations", {})
            # Check if any evaluation matches the type
            for _, eval_data in evaluations.items():
                label = eval_data.get("value", "")
                if example_type in label:
                    return result
        return None

    def _format_example(self, example: Dict[str, Any]) -> str:
        """Format an example for display in the user prompt.

        :param example: Example result dict.
        :return: Formatted string.
        """
        parts = []

        # Input
        input_data = example.get("input", {})
        parts.append(f"Input:\n{input_data}")

        # Expected output
        expected = example.get("expected_output")
        if expected:
            parts.append(f"Expected Output:\n{expected}")

        # Actual output
        output = example.get("output")
        if output:
            parts.append(f"Actual Output:\n{output}")

        # Evaluation reasoning if available
        evaluations = example.get("evaluations", {})
        if evaluations:
            for eval_name, eval_data in evaluations.items():
                if isinstance(eval_data, dict) and "reasoning" in eval_data:
                    parts.append(f"Reasoning ({eval_name}):\n{eval_data['reasoning']}")

        return "\n".join(parts)


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
        iterations: List[Dict[str, Any]],
        best_iteration: int,
    ) -> None:
        """Initialize optimization results.

        :param name: Name of the optimization run.
        :param initial_prompt: The starting prompt.
        :param iterations: List of results from each iteration. Each dict contains:
                          - iteration: int (iteration number)
                          - prompt: str (the prompt used)
                          - results: dict (full experiment results)
                          - score: float (aggregate score)
                          - experiment_url: str (URL to view experiment in Datadog UI)
        :param best_iteration: Index of the iteration with best performance.
        """
        self.name = name
        self.initial_prompt = initial_prompt
        self.iterations = iterations
        self.best_iteration = best_iteration

    @property
    def best_prompt(self) -> str:
        """Get the best performing prompt from all iterations."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return self.initial_prompt
        return self.iterations[self.best_iteration].get("prompt", self.initial_prompt)

    @property
    def best_score(self) -> Optional[float]:
        """Get the evaluation score of the best iteration."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return None
        return self.iterations[self.best_iteration].get("score")

    @property
    def best_experiment_url(self) -> Optional[str]:
        """Get the experiment URL for the best iteration."""
        if not self.iterations or self.best_iteration >= len(self.iterations):
            return None
        return self.iterations[self.best_iteration].get("experiment_url")

    @property
    def total_iterations(self) -> int:
        """Get the total number of iterations run (including baseline)."""
        return len(self.iterations)

    def get_history(self) -> List[Dict[str, Any]]:
        """Get the full optimization history with all iterations.

        Returns a list of dicts, one per iteration, containing:
        - iteration: The iteration number (0 = baseline)
        - prompt: The prompt used in this iteration
        - score: The evaluation score achieved
        - results: Full experiment results dict
        - experiment_url: URL to view experiment in Datadog UI

        :return: List of iteration results.
        """
        return self.iterations

    def get_score_history(self) -> List[float]:
        """Get list of scores across all iterations.

        :return: List of scores in iteration order.
        """
        return [it.get("score", 0.0) for it in self.iterations]

    def get_prompt_history(self) -> List[str]:
        """Get list of prompts across all iterations.

        :return: List of prompts in iteration order.
        """
        return [it.get("prompt", "") for it in self.iterations]

    def summary(self) -> str:
        """Get a human-readable summary of the optimization results.

        :return: Formatted summary string.
        """
        lines = [
            f"Optimization: {self.name}",
            f"Total iterations: {self.total_iterations}",
            f"Best iteration: {self.best_iteration}",
            f"Best score: {self.best_score:.4f}" if self.best_score is not None else "Best score: N/A",
            "\nScore progression:",
        ]

        for iteration in self.iterations:
            iter_num = iteration.get("iteration", 0)
            score = iteration.get("score")
            marker = " <- BEST" if iter_num == self.best_iteration else ""
            score_str = f"{score:.4f}" if score is not None else "N/A"
            lines.append(f"  Iteration {iter_num}: {score_str}{marker}")

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
        optimization_task: Callable[[str, str, dict], Dict[str, str]],
        dataset: Dataset,
        evaluators: List[Callable[[DatasetRecordInputType, JSONType, JSONType], JSONType]],
        project_name: str,
        config: ConfigType,
        compute_score: Callable[[Dict[str, Dict[str, Any]]], float],
        _llmobs_instance: Optional["LLMObs"] = None,
        tags: Optional[Dict[str, str]] = None,
        max_iterations: int = 5,
        summary_evaluators: Optional[List[Callable[[List, List, List, Dict], Dict]]] = None,
        stopping_condition: Optional[Callable[[Dict[str, Dict[str, Any]]], bool]] = None,
    ) -> None:
        """Initialize a prompt optimization.

        :param name: Name of the optimization run.
        :param task: Task function to execute. Must accept ``input_data`` and ``config`` parameters.
        :param optimization_task: Function to generate prompt improvements. Must accept
                                  ``system_prompt`` (str), ``user_prompt`` (str), and ``config`` (dict).
                                  Must return dict with ``prompt`` key.
        :param dataset: Dataset to run experiments on.
        :param evaluators: List of evaluator functions to measure task performance.
        :param project_name: Project name for organizing optimization runs.
        :param config: Configuration dictionary. Must contain:
                      - ``prompt``: Initial prompt template
                      - ``optimization_model_name``: Model to use for optimization
                      - ``model_name``: Model to use for task execution
        :param _llmobs_instance: Internal LLMObs instance.
        :param tags: Optional tags to associate with the optimization.
        :param max_iterations: Maximum number of optimization iterations to run.
        :param summary_evaluators: Optional list of summary evaluator functions.
                                   Each must accept (inputs: List, outputs: List, expected_outputs: List, evaluations: Dict)
                                   and return Dict with aggregated metrics.
        :param stopping_condition: Optional function to determine when to stop optimization.
                                   Takes summary_evaluations dict and returns True if should stop.
        :param compute_score: Function to compute iteration score (REQUIRED).
                              Takes summary_evaluations dict and returns float score.
        :raises ValueError: If required config parameters or compute_score are missing.
        """
        self.name = name
        self._task = task
        self._optimization_task = optimization_task
        self._dataset = dataset
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators or []
        self._stopping_condition = stopping_condition
        self._compute_score = compute_score
        self._tags: Dict[str, str] = tags or {}
        self._tags["project_name"] = project_name
        self._llmobs_instance = _llmobs_instance
        self._max_iterations = max_iterations

        # Validate required config parameters
        if not config:
            raise ValueError("config parameter is required")

        required_keys = ["prompt", "optimization_model_name", "model_name"]
        missing_keys = [key for key in required_keys if key not in config]
        if missing_keys:
            raise ValueError(f"config must contain keys: {missing_keys}")

        self._initial_prompt = config["prompt"]
        self._optimization_model_name = config["optimization_model_name"]
        self._model_name = config["model_name"]
        self._config = config
        # Optional: specify which metric to optimize (default: use first found)
        self._optimization_metric = config.get("optimization_metric", None)

    def run(
        self,
        jobs: int = 1,
    ) -> OptimizationResult:
        """Run the prompt optimization process.

        Executes a baseline experiment with the initial prompt, then iteratively
        improves the prompt based on evaluation results.

        :param jobs: Number of parallel jobs for experiment execution.
        :return: OptimizationResult containing all iteration results.
        :raises ValueError: If LLMObs is not enabled.
        """
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the optimization via `LLMObs.prompt_optimization(...)` before running."
            )

        log.info(f"Starting prompt optimization: {self.name}")

        # Track all iteration results
        all_iterations = []
        best_iteration = 0
        best_score = None
        best_prompt = None
        best_results = None

        # Run baseline experiment with initial prompt
        iteration = 0
        current_prompt = self._initial_prompt
        current_results, experiment_url = self._run_experiment(iteration, current_prompt, jobs)

        # Store baseline results
        summary_evals = current_results.get("summary_evaluations", {})
        baseline_score = self._compute_score(summary_evals)

        iteration_data = {
            "iteration": iteration,
            "prompt": current_prompt,
            "results": current_results,
            "score": baseline_score,
            "experiment_url": experiment_url,
        }
        all_iterations.append(iteration_data)
        best_score = baseline_score
        best_prompt = current_prompt
        best_results = current_results

        log.info("Baseline score: %.4f", best_score if best_score is not None else 0.0)

        # Run optimization iterations
        for i in range(1, self._max_iterations + 1):
            # Always optimize from the best prompt so far
            optimization_iteration = OptimizationIteration(
                iteration=i,
                current_prompt=best_prompt,
                current_results=best_results,
                optimization_task=self._optimization_task,
                config=self._config,
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
                "summary_evaluations": summary_evals,
                "experiment_url": experiment_url,
            }
            all_iterations.append(iteration_data)

            log.info(f"Iteration {i}")
            log.info(f"{summary_evals}")

            # Update best iteration if score improved
            if new_score is not None and (best_score is None or new_score > best_score):
                improvement = new_score - best_score if best_score is not None else new_score
                best_iteration = i
                best_score = new_score
                best_prompt = new_prompt
                best_results = new_results

            # Check stopping condition
            if self._stopping_condition and self._stopping_condition(summary_evals):
                log.info("Stopping condition met after iteration %d", i)
                break

        # Create result object with full history
        result = OptimizationResult(
            name=self.name,
            initial_prompt=self._initial_prompt,
            iterations=all_iterations,
            best_iteration=best_iteration,
        )

        # Print summary for immediate visibility
        log.info(result.summary())

        return result

    def _run_experiment(
        self,
        iteration: int,
        prompt: str,
        jobs: int,
    ) -> tuple[ExperimentResult, str]:
        """Run an experiment for a given iteration and prompt.

        :param iteration: The iteration number.
        :param prompt: The prompt to test.
        :param jobs: Number of parallel jobs.
        :return: Tuple of (experiment results dictionary, experiment URL).
        """
        iteration_name = "baseline" if iteration == 0 else f"iteration_{iteration}"

        # Update config with current prompt
        # Start with base config, then override prompt with the new one
        config_updates = {
            "model_name": self._model_name,
            "prompt": prompt
        }
        experiment_config = self._config | config_updates

        experiment = Experiment(
            name=f"{self.name}_{iteration_name}",
            project_name=self._tags["project_name"],
            dataset=self._dataset,
            task=self._task,
            evaluators=self._evaluators,
            summary_evaluators=self._summary_evaluators,
            _llmobs_instance=self._llmobs_instance,
            config=experiment_config,
        )

        experiment_results = experiment.run(
            raise_errors=True,
            jobs=jobs,
        )

        return experiment_results, experiment.url
