import abc
import dataclasses
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generic
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import TypeVar
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class _CIVisibilityRootItemIdBase:
    """This class exists for the ABC class below"""

    name: str

    def get_parent_id(self) -> "_CIVisibilityRootItemIdBase":
        return self

    def get_session_id(self) -> "_CIVisibilityRootItemIdBase":
        return self


RT = TypeVar("RT", bound="_CIVisibilityRootItemIdBase")


@dataclasses.dataclass(frozen=True)
class _CIVisibilityIdBase(abc.ABC):
    @abc.abstractmethod
    def get_parent_id(self) -> Union["_CIVisibilityIdBase", _CIVisibilityRootItemIdBase]:
        raise NotImplementedError("This method must be implemented by the subclass")

    def get_session_id(self) -> _CIVisibilityRootItemIdBase:
        return self.get_parent_id().get_session_id()


PT = TypeVar("PT", bound=Union[_CIVisibilityIdBase, _CIVisibilityRootItemIdBase])


@dataclasses.dataclass(frozen=True)
class _CIVisibilityChildItemIdBase(_CIVisibilityIdBase, Generic[PT]):
    parent_id: PT
    name: str

    def get_parent_id(self) -> PT:
        return self.parent_id


CIItemId = TypeVar("CIItemId", bound=Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase])


class _CIVisibilityAPIBase(abc.ABC):
    class SetTagArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        name: str
        value: Any

    class DeleteTagArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        name: str

    class SetTagsArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        tags: Dict[str, Any]

    class DeleteTagsArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        names: List[str]

    class AddCoverageArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        coverage_data: Dict[Path, List[Tuple[int, int]]]

    def __init__(self):
        raise NotImplementedError("This class is not meant to be instantiated")

    @staticmethod
    @abc.abstractmethod
    def discover(item_id: CIItemId, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def start(item_id: CIItemId, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def finish(
        item_id: _CIVisibilityRootItemIdBase,
        override_status: Optional[Enum],
        force_finish_children: bool = False,
        *args,
        **kwargs,
    ):
        pass

    @staticmethod
    @abc.abstractmethod
    def set_tag(item_id: CIItemId, tag_name: str, tag_value: Any):
        raise NotImplementedError("This method must be implemented by the subclass")

    @staticmethod
    @abc.abstractmethod
    def set_tags(item_id: CIItemId, tags: Dict[str, Any]):
        raise NotImplementedError("This method must be implemented by the subclass")

    @staticmethod
    @abc.abstractmethod
    def delete_tag(item_id: CIItemId, tag_name: str):
        raise NotImplementedError("This method must be implemented by the subclass")

    @staticmethod
    @abc.abstractmethod
    def delete_tags(item_id: CIItemId, tag_names: List[str]):
        raise NotImplementedError("This method must be implemented by the subclass")
