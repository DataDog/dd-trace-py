from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import TypedDict
from typing import Union

from typing_extensions import NotRequired


JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]


class DatasetRecord(TypedDict):
    input_data: NonNoneJSONType
    expected_output: JSONType
    metadata: Dict[str, Any]
    record_id: NotRequired[Optional[str]]


class Dataset:
    name: str
    _id: str
    _data: List[DatasetRecord]

    def __init__(self, name: str, dataset_id: str, data: List[DatasetRecord]) -> None:
        self.name = name
        self._id = dataset_id
        self._data = data


class Experiment:
    def __init__(
        self,
        name: str,
        task: Callable[[Dict[str, NonNoneJSONType]], JSONType],
        dataset: Dataset,
        evaluators: List[Callable[[NonNoneJSONType, JSONType, JSONType], JSONType]],
        description: str = "",
        config: Optional[Dict[str, Any]] = None,
        _llmobs: Optional[Any] = None,  # LLMObs service (cannot import here due to circular dependency)
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._description = description
        self._config: Dict[str, Any] = config or {}
        self._llmobs = _llmobs
        self._id: Optional[str] = None

    def run(self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None) -> None:
        task_results = self._run_task(jobs, raise_errors, sample_size)
        self._run_evaluators(task_results, raise_errors=raise_errors)
        return

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[Any]:
        return []

    def _run_evaluators(self, task_results, raise_errors: bool = False) -> None:
        pass
