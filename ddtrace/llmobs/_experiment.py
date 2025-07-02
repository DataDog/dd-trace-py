from typing import Any
from typing import Dict
from typing import List
from typing import NotRequired
from typing import Optional
from typing import TypedDict
from typing import Union


JSONType = Union[str, int, float, bool, None, List["JSONType"], Dict[str, "JSONType"]]


class DatasetRecord(TypedDict):
    input: JSONType
    expected_output: JSONType
    record_id: NotRequired[Optional[str]]
    metadata: NotRequired[Optional[Dict[str, Any]]]


class Dataset:
    _name: str
    _id: str
    _data: List[DatasetRecord]

    def __init__(self, name: str, id: str, data: List[DatasetRecord]) -> None:
        self._name = name
        self._id = id
        self._data = data

    def __str__(self) -> str:
        return f"Dataset(name={self._name}, id={self._id}, data={self._data})"


class Experiment:
    def __init__(self, name: str, dataset: Dataset, description: str = "") -> None:
        self.name = name
        self._dataset = dataset
        self._experiment_id: Optional[str] = None
        self._project_id: Optional[str] = None

    def run(self):
        pass
