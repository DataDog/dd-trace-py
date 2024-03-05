import abc
import dataclasses
from enum import Enum
from typing import Any
from typing import Dict
from typing import Generic
from typing import List
from typing import Optional
from typing_extensions import Self
from typing import TypeVar
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityOrphanItemIdType:
    pass


# @dataclasses.dataclass(frozen=True)
# class _CIVisibilityIdBase(abc.ABC, Generic[T]):
#     @abc.abstractmethod
#     def get_parent_id(self) -> "_CIVisibilityIdBase":
#         raise NotImplementedError("This method must be implemented by the subclass")

#     @abc.abstractmethod
#     def get_session_id(self) -> "_CIVisibilityIdBase":
#         raise NotImplementedError("This method must be implemented by the subclass")




@dataclasses.dataclass(frozen=True)
class _CIVisibilityOrphanItemIdBase():
    """This class exists for the ABC class below"""

    name: str

    def get_parent_id(self) -> Self:
        return self

    def get_session_id(self) -> Self:
        return self

PT = TypeVar("PT", bound=_CIVisibilityOrphanItemIdBase)

@dataclasses.dataclass(frozen=True)
class _CIVisibilityChildItemIdBase(_CIVisibilityOrphanItemIdBase, Generic[PT]):
    parent_id: PT
    name: str

    def get_parent_id(self) -> PT:
        return self.parent_id

    def get_session_id(self) -> _CIVisibilityOrphanItemIdBase:
        p = self.get_parent_id()
        s = p.get_session_id()
        return self.get_parent_id().get_session_id()


# class CIVisibilityChildItemIdType(_CIVisibilityChildItemIdBase):
#     pass


# CIItemIdType = TypeVar("CIItemIdType", bound=Union[CIVisibilityChildItemIdType, CIVisibilityOrphanItemIdType])


class _CIVisibilityAPIBase(abc.ABC):
    def __init__(self):
        raise NotImplementedError("This class is not meant to be instantiated")

    @staticmethod
    @abc.abstractmethod
    def discover(item_id: _CIVisibilityOrphanItemIdBase, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def start(item_id: _CIVisibilityOrphanItemIdBase, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def finish(
        item_id: _CIVisibilityOrphanItemIdBase,
        override_status: Optional[Enum],
        force_finish_children: bool = False,
        *args,
        **kwargs,
    ):
        pass

    @staticmethod
    def set_tag(item_id: _CIVisibilityOrphanItemIdBase, tag_name: str, tag_value: Any, recurse: bool = False):
        log.debug("Setting tag for item %s: %s=%s", item_id, tag_name, tag_value)

    @staticmethod
    def set_tags(item_id: _CIVisibilityOrphanItemIdBase, tags: Dict[str, Any], recurse: bool = False):
        log.debug("Setting tags for item %s: %s", item_id, tags)

    @staticmethod
    def delete_tag(item_id: _CIVisibilityOrphanItemIdBase, tag_name: str, recurse: bool = False):
        log.debug("Deleting tag for item %s: %s", item_id, tag_name)

    @staticmethod
    def delete_tags(item_id: _CIVisibilityOrphanItemIdBase, tag_names: List[str], recurse: bool = False):
        log.debug("Deleting tags for item %s: %s", item_id, tag_names)
