import abc
import dataclasses
import functools
from typing import Any
from typing import Dict
from typing import Generic
from typing import List
from typing import Optional
from typing import TypeVar

from ddtrace import Span
from ddtrace.ext.ci_visibility._ci_visibility_base import CIVisibilityItemIdType
from ddtrace.internal.ci_visibility.constants import DEFAULT_OPERATION_NAMES
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class CIVisibilitySessionSettings:
    session_operation_name: Optional[str] = DEFAULT_OPERATION_NAMES.SESSION.value
    module_operation_name: Optional[str] = DEFAULT_OPERATION_NAMES.MODULE.value
    suite_operation_name: Optional[str] = DEFAULT_OPERATION_NAMES.SUITE.value
    test_operation_name: Optional[str] = DEFAULT_OPERATION_NAMES.TEST.value
    reject_unknown_items: Optional[bool] = True
    reject_duplicates: Optional[bool] = True


IDT = TypeVar("IDT", bound=CIVisibilityItemIdType)


class CIVisibilityItemBase(abc.ABC, Generic[IDT]):
    def __init__(
        self,
        item_id: IDT,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, Any]],
    ):
        self.item_id: IDT = item_id
        self.name = self.item_id.name
        self._session_settings = session_settings
        self.span: Optional[Span] = None
        self._tags = initial_tags if initial_tags else {}
        self._is_finished = False
        self._children = None

    def _require_span(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.span is None:
                log.debug("Method {func} called on item , but no span is set")
                return
            return func(*args, **kwargs)

        return wrapper

    def _require_not_finished(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if self._is_finished:
                log.warning("Method %s called on item %s, but it is already finished", func, self.item_id)
                return
            return func(*args, **kwargs)

        return wrapper

    @abc.abstractmethod
    def start(self):
        raise NotImplementedError

    @abc.abstractmethod
    def finish(self):
        raise NotImplementedError

    # @_require_not_finished
    def set_tag(self, tag_name: str, tag_value: Any, recurse: bool = False) -> None:
        self._tags[tag_name] = tag_value

    # @_require_not_finished
    def set_tags(self, tags: Dict[str, Any], recurse: bool = False) -> None:
        for tag in tags:
            self._tags[tag] = tags[tag]

    # @_require_not_finished
    def get_tag(self, tag_name: str) -> Any:
        return self._tags[tag_name]

    # @_require_not_finished
    def get_tags(self, tag_names: List[str]) -> Dict[str, Any]:
        tags = {}
        for tag_name in tag_names:
            tags[tag_name] = self._tags[tag_name]

        return tags

    # @_require_not_finished
    def delete_tag(self, tag_name: str, recurse=False) -> None:
        del self._tags[tag_name]

    # @_require_not_finished
    def delete_tags(self, tag_names: List[str], recurse=False) -> None:
        for tag_name in tag_names:
            del self._tags[tag_name]


class CIVisibilityItemBaseType:
    pass


CIDT = TypeVar("CIDT", bound=CIVisibilityItemIdType)
ITEMT = TypeVar("ITEMT", bound=CIVisibilityItemBaseType)


class CIVisibilityParentItem(CIVisibilityItemBase[IDT], Generic[IDT, CIDT, ITEMT]):
    def __init__(
        self,
        item_id: IDT,
        session_settings: CIVisibilitySessionSettings,
        initial_tags: Optional[Dict[str, Any]],
    ):
        super().__init__(item_id, session_settings, initial_tags)
        self.children: Dict[CIDT, ITEMT] = {}

    def add_child(self, child: ITEMT):
        if self._session_settings.reject_duplicates and child.item_id in self.children:
            error_msg = f"{child.item_id} already exists in {self.item_id}'s children"
            log.warning(error_msg)
            raise CIVisibilityDataError(error_msg)
        self.children[child.item_id] = child

    def get_child_by_id(self, child_id: CIDT) -> ITEMT:
        if child_id in self.children:
            return self.children[child_id]
        error_msg = f"{child_id} not found in {self.item_id}'s children"
        raise CIVisibilityDataError(error_msg)

    # @CIVisibilityItemBase._require_not_finished
    def set_tag(self, tag_name: str, tag_value: Any, recurse: bool = False) -> None:
        super().set_tag(tag_name, tag_value)
        if recurse:
            for child in self.children.values():
                child.set_tag(tag_name, tag_value, recurse=True)

    # @CIVisibilityItemBase._require_not_finished
    def set_tags(self, tags: Dict[str, Any], recurse: bool = False) -> None:
        super().set_tags(tags)
        if recurse and self.children is not None:
            for child in self.children.values():
                child.set_tags(tags, recurse=True)

    # @CIVisibilityItemBase._require_not_finished
    def delete_tag(self, tag_name: str, recurse=False) -> None:
        super().delete_tag(tag_name)
        if recurse:
            for child in self.children.values():
                child.delete_tag(tag_name, recurse=True)

    # @CIVisibilityItemBase._require_not_finished
    def delete_tags(self, tag_names: List[str], recurse=False) -> None:
        super().delete_tags(tag_names)
        if recurse:
            for child in self.children.values():
                child.delete_tags(tag_names, recurse=True)
