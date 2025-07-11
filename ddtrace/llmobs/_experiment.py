from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import TypedDict
from typing import Union

from typing_extensions import NotRequired


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs


JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]


class DatasetRecord(TypedDict):
    input_data: NonNoneJSONType
    expected_output: JSONType
    metadata: Dict[str, Any]
    record_id: NotRequired[Optional[str]]


class Dataset:
    name: str
    description: str
    _id: str
    _data: List[DatasetRecord]
    _version: int

    def __init__(self, name: str, dataset_id: str, data: List[DatasetRecord], description: str, version: int) -> None:
        self.name = name
        self.description = description
        self._id = dataset_id
        self._data = data
        self._version = version


class Experiment:
    def __init__(
        self,
        name: str,
        task: Callable[[Dict[str, NonNoneJSONType]], JSONType],
        dataset: Dataset,
        evaluators: List[Callable[[NonNoneJSONType, JSONType, JSONType], JSONType]],
        project_name: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        _llmobs_instance: Optional["LLMObs"] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._description = description
        self._tags = tags or []
        self._config: Dict[str, Any] = config or {}
        self._llmobs_instance = _llmobs_instance

        if not project_name:
            raise ValueError(
                "project_name must be provided for the experiment, either configured via the `DD_LLMOBS_PROJECT_NAME` "
                "environment variable, or an argument to `LLMObs.enable(project_name=...)`, "
                "or as an argument to `LLMObs.experiment(project_name=...)`."
            )
        self._project_name = project_name
        self._project_id: Optional[str] = None
        self._id: Optional[str] = None

    def run(self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None) -> None:
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError(
                "LLMObs is not enabled. Ensure LLM Observability is enabled via `LLMObs.enable(...)` "
                "and create the experiment via `LLMObs.experiment(...)` before running the experiment."
            )
        experiment_id, experiment_name = self._llmobs_instance._create_experiment(
            name=self.name,
            dataset_id=self._dataset._id,
            project_name=self._project_name,
            dataset_version=self._dataset._version,
            exp_config=self._config,
            tags=self._tags,
            description=self._description,
        )
        self._id = experiment_id
        if experiment_name != self.name:
            self.name = experiment_name
        task_results = self._run_task(jobs, raise_errors, sample_size)
        self._run_evaluators(task_results, raise_errors=raise_errors)
        return

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[Any]:
        return []

    def _run_evaluators(self, task_results, raise_errors: bool = False) -> None:
        pass
