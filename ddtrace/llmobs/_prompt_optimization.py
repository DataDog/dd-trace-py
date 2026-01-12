from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import Experiment

from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
OptimizationConfigType = Dict[str, JSONType]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]
ExperimentConfigType = Dict[str, JSONType]
DatasetRecordInputType = Dict[str, NonNoneJSONType]

class OptimizationIteration:
    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: JSONType,
    ) -> None:
        self.iteration = iteration
        self.current_prompt = current_prompt
        self.current_results = current_results

    def run(self):
        print(f"Iteration {self.iteration}")
        return self.current_prompt

class OptimizationResult:
    ...

class PromptOptimization:
    def __init__(
        self,
        name: str,
        task: Callable[[DatasetRecordInputType, Optional[ExperimentConfigType]], JSONType],
        optimization_task: Callable[[DatasetRecordInputType, Optional[ExperimentConfigType]], JSONType],
        dataset: Dataset,
        evaluators: List[Callable[[DatasetRecordInputType, JSONType, JSONType], JSONType]],
        project_name: str,
        config: OptimizationConfigType,
        _llmobs_instance: Optional["LLMObs"] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._optimization_task = optimization_task
        self._dataset = dataset
        self._evaluators = evaluators
        self._tags: Dict[str, str] = tags or {}
        self._tags["project_name"] = project_name
        self._llmobs_instance = _llmobs_instance

        # Config must have a prompt param
        assert "prompt" in config
        assert "optimization_model_name" in config
        assert "model_name" in config
        self._initial_prompt = config["prompt"]
        self._optimization_model_name = config["optimization_model_name"]
        self._model_name = config["model_name"]

    def _process_templated_row(self, row: Dict[str, Any]):
        row["input_data"] = {
            "data": render_template(row["input_data"], row["metadata"]),
            "metadata": row['metadata']
        }
        return row

    def run(
        self,
        jobs: int = 1,
    ) -> OptimizationResult:
        print("Optimization running")
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )

        iteration = 0
        # 1. Run the experiment with the initial prompt and gather results
        current_prompt = self._initial_prompt
        current_results = self._run_experiment(iteration)
        print(current_results["summary_evaluations"])

        for i in range(1, 3):
            optimization_iteration = OptimizationIteration(
                iteration + i,
                current_prompt,
                current_results,
            )
            current_prompt = optimization_iteration.run()
            current_results = self._run_experiment(iteration)




    def _run_experiment(
        self,
        iteration: int,
    ):
        if iteration == 0:
            print("Evaluation of the initial")
        else:
            print(f"Iteration {iteration}")
        experiment = Experiment(
            name=f"generic_prompt_optimizer_iteration_{iteration + 1}",
            project_name=self._tags["project_name"],
            dataset=self._dataset,
            task=self._task,
            evaluators=self._evaluators,
            _llmobs_instance=self._llmobs_instance,
            config={
                "model_name": self._model_name
            },
        )

        experiment_results = experiment.run(
            raise_errors=True,
            jobs=1
        )

        return experiment_results
