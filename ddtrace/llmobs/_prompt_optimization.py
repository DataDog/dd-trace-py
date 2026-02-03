"""Prompt optimization framework for iteratively improving LLM prompts."""

from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecordInputType
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import JSONType
from ddtrace.llmobs._optimizers import CandidateSelector
from ddtrace.llmobs._optimizers import Metaprompting
from ddtrace.llmobs._optimizers import MetapromptingSelector
from ddtrace.llmobs._optimizers import MIPRO
from ddtrace.llmobs._optimizers import MIPROSelector
from ddtrace.llmobs._optimizers import OptimizationIteration


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs


log = get_logger(__name__)


# Registry of available optimizer implementations
# Maps optimizer name to (OptimizationIteration class, CandidateSelector class)
OPTIMIZER_REGISTRY: Dict[str, Tuple[type[OptimizationIteration], type[CandidateSelector]]] = {
    "metaprompting": (Metaprompting, MetapromptingSelector),
    "mipro": (MIPRO, MIPROSelector),
}


class IterationData(TypedDict):
    """Data for a single optimization iteration."""

    iteration: int
    prompt: str
    results: ExperimentResult
    score: float
    experiment_url: str
    summary_evaluations: Dict[str, Dict[str, JSONType]]


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
        iterations: List[IterationData],
        best_iteration: int,
    ) -> None:
        """Initialize optimization results.

        :param name: Name of the optimization run.
        :param initial_prompt: The starting prompt.
        :param iterations: List of results from each iteration (IterationData).
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

    def get_history(self) -> List[IterationData]:
        """Get the full optimization history with all iterations.

        Returns a list of IterationData dicts, one per iteration.

        :return: List of iteration results.
        """
        return self.iterations

    def get_score_history(self) -> List[float]:
        """Get list of scores across all iterations.

        :return: List of scores in iteration order.
        """
        return [it["score"] for it in self.iterations]

    def get_prompt_history(self) -> List[str]:
        """Get list of prompts across all iterations.

        :return: List of prompts in iteration order.
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

        # Add best iteration's summary evaluations
        for iteration in self.iterations:
            if iteration["iteration"] == self.best_iteration:
                summary_evals = iteration["summary_evaluations"]
                if summary_evals:
                    lines.append(f"\nBest iteration summary evaluations:\n{summary_evals}")
                break

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
        evaluators: List[
            Union[
                Callable[[DatasetRecordInputType, JSONType, JSONType], Union[JSONType, EvaluatorResult]],
                BaseEvaluator,
            ]
        ],
        project_name: str,
        config: ConfigType,
        summary_evaluators: List[
            Union[
                Callable[
                    [
                        List[DatasetRecordInputType],
                        List[JSONType],
                        List[JSONType],
                        Dict[str, List[JSONType]],
                    ],
                    JSONType,
                ],
                BaseSummaryEvaluator,
            ]
        ],
        compute_score: Callable[[Dict[str, Dict[str, Any]]], float],
        labelization_function: Optional[Callable[[Dict[str, Any]], str]],
        _llmobs_instance: Optional["LLMObs"] = None,
        tags: Optional[Dict[str, str]] = None,
        max_iterations: int = 5,
        stopping_condition: Optional[Callable[[Dict[str, Dict[str, Any]]], bool]] = None,
        optimizer_class: str = "metaprompting",
    ) -> None:
        """Initialize a prompt optimization.

        :param name: Name of the optimization run.
        :param task: Task function to execute. Must accept ``input_data`` and ``config`` parameters.
        :param optimization_task: Function to generate prompt improvements. Must accept
                                  ``system_prompt`` (str), ``user_prompt`` (str), and ``config`` (dict).
                                  Must return the new prompt.
        :param dataset: Dataset to run experiments on.
        :param evaluators: List of evaluators to measure task performance. Can be either
                          class-based evaluators (inheriting from BaseEvaluator) or function-based
                          evaluators that accept (input_data, output_data, expected_output) parameters.
        :param project_name: Project name for organizing optimization runs.
        :param config: Configuration dictionary. Must contain:
                      - ``prompt`` (mandatory): Initial prompt template
                      - ``model_name`` (optional): Model to use for task execution
                      - ``evaluation_output_format`` (optional): the output format wanted
                      - ``runs`` (optional): The number of times to run the experiment, or, run the task for every
                                             dataset record the defined number of times.
        :param summary_evaluators: List of summary evaluators (REQUIRED). Can be either
                                   class-based evaluators (inheriting from BaseSummaryEvaluator) or function-based
                                   evaluators that accept (inputs: List, outputs: List, expected_outputs: List,
                                   evaluations: Dict) and return aggregated metrics.
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
        :param optimizer_class: Name of the optimizer implementation to use. Defaults to "metaprompting".
                               Available optimizers:
                               - "metaprompting": Single-candidate prompt optimization using LLM-as-a-judge
                               - "mipro": Multi-candidate optimization with Bayesian search (requires optuna)
        :raises ValueError: If required config parameters, compute_score are missing, or optimizer_class is invalid.
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
        self._tags: Dict[str, str] = tags or {}
        self._tags["project_name"] = project_name
        self._llmobs_instance = _llmobs_instance
        self._max_iterations = max_iterations

        # Validate and get optimizer and selector classes from registry
        if optimizer_class not in OPTIMIZER_REGISTRY:
            raise ValueError(
                f"Unknown optimizer: '{optimizer_class}'. Available optimizers: {list(OPTIMIZER_REGISTRY.keys())}"
            )
        self._optimizer_class, self._selector_class = OPTIMIZER_REGISTRY[optimizer_class]

        # Validate required config parameters
        if not config:
            raise ValueError("config parameter is required")

        if not isinstance(config, dict) or "prompt" not in config:
            raise ValueError("config must contain a 'prompt' key")

        self._initial_prompt = config["prompt"]
        self._model_name = config.get("model_name")
        self._config = config
        self._project_name = project_name

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

        print("Starting prompt optimization: %s", self.name)

        # Track all iteration results
        all_iterations: List[IterationData] = []
        best_iteration = 0
        best_score = None
        best_prompt = None
        best_results = None

        # Create candidate selector for this optimizer
        candidate_selector = self._selector_class(
            dataset=self._dataset,
            task=self._task,
            evaluators=self._evaluators,
            summary_evaluators=self._summary_evaluators,
            compute_score=self._compute_score,
            config=self._config,
            llmobs_instance=self._llmobs_instance,
            project_name=self._project_name,
        )

        # Run baseline experiment with initial prompt
        iteration = 0
        current_prompt = str(self._initial_prompt)
        iteration_name = "baseline"

        # Use selector to run experiment on baseline
        current_prompt, current_results, experiment_url = self._run_experiment_via_selector(
            candidate_selector, [current_prompt], iteration, iteration_name, jobs
        )

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

        print("Baseline score: %.3f", best_score)

        # Run optimization iterations
        for i in range(1, self._max_iterations + 1):
            # Always optimize from the best prompt so far
            # Prepare kwargs for optimizer initialization
            optimizer_kwargs = {
                "iteration": i,
                "current_prompt": best_prompt,
                "current_results": best_results,
                "optimization_task": self._optimization_task,
                "config": self._config,
                "labelization_function": self._labelization_function,
            }

            # Add dataset for MIPRO (supports few-shot example optimization)
            from ddtrace.llmobs._optimizers import MIPRO

            if self._optimizer_class == MIPRO:
                optimizer_kwargs["dataset"] = self._dataset

            optimization_iteration = self._optimizer_class(**optimizer_kwargs)

            # Generate candidate prompts (list)
            candidate_prompts = optimization_iteration.run()

            print("Iteration %d: Generated %d candidate(s)", i, len(candidate_prompts))

            # Use selector to evaluate candidates and pick the best
            iteration_name = f"iteration_{i}"
            new_prompt, new_results, experiment_url = self._run_experiment_via_selector(
                candidate_selector, candidate_prompts, i, iteration_name, jobs
            )

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

            print("Iteration %s", i)
            print("%s", summary_evals)

            # Update best iteration if score improved
            if new_score is not None and (best_score is None or new_score > best_score):
                best_iteration = i
                best_score = new_score
                best_prompt = new_prompt
                best_results = new_results

            # Check stopping condition
            if self._stopping_condition and self._stopping_condition(summary_evals):
                print("Stopping condition met after iteration %s", i)
                break

        # Create result object with full history
        result = OptimizationResult(
            name=self.name,
            initial_prompt=str(self._initial_prompt),
            iterations=all_iterations,
            best_iteration=best_iteration,
        )

        return result

    def _run_experiment_via_selector(
        self,
        selector: CandidateSelector,
        candidates: List[str],
        iteration: int,
        iteration_name: str,
        jobs: int,
    ) -> tuple[str, ExperimentResult, str]:
        """Run experiment(s) via the candidate selector to pick the best candidate.

        :param selector: The CandidateSelector instance to use.
        :param candidates: List of candidate prompts to evaluate.
        :param iteration: The iteration number.
        :param iteration_name: Name for this iteration (e.g., "baseline", "iteration_1").
        :param jobs: Number of parallel jobs.
        :return: Tuple of (best_prompt, experiment results, experiment URL).
        """
        experiment_name = f"{self.name}_{iteration_name}"

        # Use selector to evaluate candidates and return the best
        best_prompt, best_results, experiment_url = selector.select_best_candidate(
            candidates=candidates,
            iteration=iteration,
            experiment_name=experiment_name,
            jobs=jobs,
        )

        return best_prompt, best_results, experiment_url
