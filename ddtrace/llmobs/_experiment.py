import inspect
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import TypedDict
from typing import Union

from typing_extensions import NotRequired
import wrapt


JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]
NonNoneJSONType = Union[str, int, float, bool, List[JSONType], Dict[str, JSONType]]


class DatasetRecord(TypedDict):
    input: NonNoneJSONType
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


class ExperimentTaskWrapper(wrapt.ObjectProxy):
    def __init__(self, wrapped: Callable):
        super().__init__(wrapped)
        sig = inspect.signature(wrapped)
        params = sig.parameters
        if "input" not in params:
            raise TypeError("Task function must have an 'input' parameter.")
        self._self_accept_config = "config" in params

    def __call__(self, input: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Any:
        if self._self_accept_config:
            return self.__wrapped__(input, config)
        return self.__wrapped__(input)


class ExperimentEvaluatorWrapper(wrapt.ObjectProxy):
    def __init__(self, wrapped: Callable):
        super().__init__(wrapped)
        sig = inspect.signature(wrapped)
        params = sig.parameters
        required_params = ("input", "output", "expected_output")
        if not all(param in params for param in required_params):
            raise TypeError("Evaluator function must have parameters {}.".format(required_params))

    def __call__(self, *args, **kwargs) -> Any:
        return self.__wrapped__(*args, **kwargs)


class Experiment:
    def __init__(
        self,
        name: str,
        task: ExperimentTaskWrapper,
        dataset: Dataset,
        evaluators: List[ExperimentEvaluatorWrapper],
        _llmobs: Any,
        description: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self._task = task
        self._dataset = dataset
        self._evaluators = evaluators
        self._description = description
        self._config: Dict[str, Any] = config or {}
        self._llmobs = _llmobs
        self._id: Optional[str] = None
        self._project_id: Optional[str] = None

    def run(self, jobs: int = 1, raise_errors: bool = False, sample_size: Optional[int] = None) -> None:
        task_results = self._run_task(jobs, raise_errors, sample_size)
        self._run_evaluators(task_results, raise_errors=raise_errors)
        return

    def _run_task(self, jobs: int, raise_errors: bool = False, sample_size: Optional[int] = None) -> List[Any]:
        return []

    def _run_evaluators(self, task_results, raise_errors: bool = False) -> None:
        pass
