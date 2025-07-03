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
    name: str
    _id: str
    _data: List[DatasetRecord]

    def __init__(self, name: str, dataset_id: str, data: List[DatasetRecord]) -> None:
        self.name = name
        self._id = dataset_id
        self._data = data
