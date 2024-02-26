import abc
import dataclasses
from enum import Enum
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class _CIVisibilityItemIdBase:
    """This class exists for the ABC class below"""

    def get_parent(self):
        raise NotImplementedError("Not implemented in base class")

    def get_session_id(self):
        raise NotImplementedError("Not implemented in base class")


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
