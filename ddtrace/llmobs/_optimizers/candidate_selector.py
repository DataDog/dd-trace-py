"""Base class and implementations for selecting the best prompt candidate."""

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecordInputType
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import Experiment
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import JSONType


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs


log = get_logger(__name__)


class CandidateSelector(ABC):
    """Base class for selecting the best prompt candidate from a list.

    Each optimizer implementation should have a corresponding CandidateSelector
    that knows how to evaluate and select the best prompt from multiple candidates.

    Example usage::

        selector = MetapromptingSelector(
            dataset=dataset,
            task=task_fn,
            evaluators=[...],
            summary_evaluators=[...],
            compute_score=score_fn,
            config=config,
            llmobs_instance=llmobs,
            project_name="my_project"
        )

        best_prompt, best_results, url = selector.select_best_candidate(
            candidates=["prompt1", "prompt2"],
            iteration=1,
            experiment_name="test_experiment",
            jobs=1
        )
    """

    def __init__(
        self,
        dataset: Dataset,
        task: Callable[[DatasetRecordInputType, Optional[ConfigType]], JSONType],
        evaluators: List[
            Union[
                Callable[[DatasetRecordInputType, JSONType, JSONType], Union[JSONType, EvaluatorResult]],
                BaseEvaluator,
            ]
        ],
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
        config: ConfigType,
        llmobs_instance: "LLMObs",
        project_name: str,
    ) -> None:
        """Initialize a candidate selector.

        :param dataset: Dataset to run experiments on.
        :param task: Task function to execute.
        :param evaluators: List of evaluators to measure task performance.
        :param summary_evaluators: List of summary evaluators.
        :param compute_score: Function to compute score from summary evaluations.
        :param config: Base configuration dictionary.
        :param llmobs_instance: LLMObs instance for experiment creation.
        :param project_name: Project name for organizing experiments.
        """
        self._dataset = dataset
        self._task = task
        self._evaluators = evaluators
        self._summary_evaluators = summary_evaluators
        self._compute_score = compute_score
        self._config = config
        self._llmobs_instance = llmobs_instance
        self._project_name = project_name

    @abstractmethod
    def select_best_candidate(
        self,
        candidates: List[str],
        iteration: int,
        experiment_name: str,
        jobs: int,
    ) -> Tuple[str, ExperimentResult, str]:
        """Select the best candidate from a list of prompts.

        This method should run experiments on the candidates and return the best one.

        :param candidates: List of candidate prompt strings.
        :param iteration: The iteration number.
        :param experiment_name: Base name for experiments.
        :param jobs: Number of parallel jobs for experiment execution.
        :return: Tuple of (best_prompt, best_results, experiment_url).
        :raises NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError("Subclasses must implement select_best_candidate()")

    def _run_experiment(
        self,
        prompt: str,
        experiment_name: str,
        model_name: Optional[str],
        jobs: int,
        runs: Optional[int] = None,
    ) -> Tuple[ExperimentResult, str]:
        """Run an experiment for a given prompt.

        :param prompt: The prompt to test.
        :param experiment_name: Name for this experiment.
        :param model_name: Model name to use.
        :param jobs: Number of parallel jobs.
        :param runs: Number of times to run each dataset record.
        :return: Tuple of (experiment results, experiment URL).
        """
        # Build experiment config with the prompt
        config_updates = {"prompt": prompt}
        if model_name:
            config_updates["model_name"] = model_name

        experiment_config = self._config | config_updates

        experiment = Experiment(
            name=experiment_name,
            project_name=self._project_name,
            dataset=self._dataset,
            task=self._task,
            evaluators=self._evaluators,
            summary_evaluators=self._summary_evaluators,
            _llmobs_instance=self._llmobs_instance,
            config=experiment_config,
            runs=runs,
        )

        experiment_results = experiment.run(
            raise_errors=True,
            jobs=jobs,
        )

        return experiment_results, experiment.url


class MetapromptingSelector(CandidateSelector):
    """Candidate selector for Metaprompting optimizer.

    Since Metaprompting generates only a single candidate, this selector
    simply runs an experiment on that candidate and returns it.
    """

    def select_best_candidate(
        self,
        candidates: List[str],
        iteration: int,
        experiment_name: str,
        jobs: int,
    ) -> Tuple[str, ExperimentResult, str]:
        """Select the best candidate (the only candidate for Metaprompting).

        :param candidates: List containing a single candidate prompt.
        :param iteration: The iteration number.
        :param experiment_name: Base name for the experiment.
        :param jobs: Number of parallel jobs for experiment execution.
        :return: Tuple of (prompt, results, experiment_url).
        :raises ValueError: If no candidates are provided.
        """
        if not candidates:
            raise ValueError("No candidates provided for selection")

        if len(candidates) > 1:
            log.warning(
                "MetapromptingSelector received %d candidates but expects 1. Using first candidate.",
                len(candidates),
            )

        # For Metaprompting, there's only one candidate
        prompt = candidates[0]

        # Get model name and runs from config
        model_name = self._config.get("model_name")
        runs_value = self._config.get("runs")
        runs_int: Optional[int] = None
        if runs_value is not None and isinstance(runs_value, int):
            runs_int = runs_value

        # Run experiment on the single candidate
        log.info("Running experiment for iteration %d with single candidate", iteration)
        results, url = self._run_experiment(
            prompt=prompt,
            experiment_name=experiment_name,
            model_name=model_name,
            jobs=jobs,
            runs=runs_int,
        )

        return prompt, results, url
