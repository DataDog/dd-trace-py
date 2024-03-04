import abc
import dataclasses
from enum import Enum
from typing import Any
from typing import Dict
from typing import Generic
from typing import List
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CIVisibilityItemIdType:
    pass


@dataclasses.dataclass(frozen=True)
class _CIVisibilityItemIdBase:
    """This class exists for the ABC class below"""

    name: str

    def get_parent_id(self) -> "_CIVisibilityItemIdBase":
        return self

    def get_session_id(self) -> "_CIVisibilityItemIdBase":
        return self


T = TypeVar("T", bound=Union[CIVisibilityItemIdType, _CIVisibilityItemIdBase])


@dataclasses.dataclass(frozen=True)
class _CIVisibilityChildItemIdBase(Generic[T]):
    parent_id: T
    name: str

    def get_parent_id(self) -> T:
        return self.parent_id

    def get_session_id(self) -> T:
        return self.get_parent_id().get_session_id()


class _CIVisibilityAPIBase(abc.ABC):
    def __init__(self):
        raise NotImplementedError("This class is not meant to be instantiated")

    @staticmethod
    @abc.abstractmethod
    def discover(item_id: _CIVisibilityItemIdBase, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def start(item_id: _CIVisibilityItemIdBase, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def finish(
        item_id: _CIVisibilityItemIdBase,
        override_status: Optional[Enum],
        force_finish_children: bool = False,
        *args,
        **kwargs,
    ):
        pass

    @staticmethod
    def set_tag(item_id: _CIVisibilityItemIdBase, tag_name: str, tag_value: Any, recurse: bool = False):
        log.debug("Setting tag for item %s: %s=%s", item_id, tag_name, tag_value)

    @staticmethod
    def set_tags(item_id: _CIVisibilityItemIdBase, tags: Dict[str, Any], recurse: bool = False):
        log.debug("Setting tags for item %s: %s", item_id, tags)

    @staticmethod
    def delete_tag(item_id: _CIVisibilityItemIdBase, tag_name: str, recurse: bool = False):
        log.debug("Deleting tag for item %s: %s", item_id, tag_name)

    @staticmethod
    def delete_tags(item_id: _CIVisibilityItemIdBase, tag_names: List[str], recurse: bool = False):
        log.debug("Deleting tags for item %s: %s", item_id, tag_names)
