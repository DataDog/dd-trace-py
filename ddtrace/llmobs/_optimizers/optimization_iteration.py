"""Base class for prompt optimization iterations."""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import ExperimentResult


class OptimizationIteration(ABC):
    """Base class for optimization iteration strategies.

    This abstract class defines the interface that all optimizer implementations
    must follow. Each optimizer strategy (e.g., Metaprompting, DSPy GEPA) should
    extend this class and implement the run() method with their specific logic.

    Example usage::

        class CustomOptimizer(OptimizationIteration):
            def run(self) -> str:
                # Custom optimization logic
                return improved_prompt

        # Use in PromptOptimization
        optimization = LLMObs.prompt_optimization(
            ...,
            optimizer_class=CustomOptimizer
        )
    """

    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: ExperimentResult,
        optimization_task: Callable,
        config: ConfigType,
        labelization_function: Optional[Callable[[Dict[str, Any]], str]],
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

    @abstractmethod
    def run(self) -> str:
        """Run the optimization iteration to generate an improved prompt.

        This method must be implemented by each optimizer strategy.
        It should analyze the current prompt and results, then return an improved prompt.

        :return: The improved prompt string.
        :raises NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError("Subclasses must implement the run() method")
