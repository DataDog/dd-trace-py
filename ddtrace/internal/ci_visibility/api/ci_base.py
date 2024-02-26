import abc
import dataclasses
import functools
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityItemIdBase
from ddtrace.internal.ci_visibility.constants import DEFAULT_OPERATION_NAMES
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


class CIVisibilityItemBase(abc.ABC):
    def __init__(self, item_id: _CIVisibilityItemIdBase, initial_tags: Dict[str, Any]):
        self.item_id = item_id
        self.span: Optional[Span] = None
        self._tags = initial_tags if initial_tags else {}

    @staticmethod
    def _require_span(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.span is None:
                log.debug("Method {func} called on item , but no span is set")
            return func(*args, **kwargs)

        return wrapper

    @abc.abstractmethod
    def start(self):
        pass

    @abc.abstractmethod
    def finish(self):
        pass

    @abc.abstractmethod
    def set_tag(self, tag_name: str, tag_value: Any, recurse: bool = False) -> None:
        pass

    @abc.abstractmethod
    def set_tags(self, tags: Dict[str, Any], recurse: bool = False) -> None:
        pass

    @abc.abstractmethod
    def get_tag(self) -> Any:
        pass

    @abc.abstractmethod
    def get_tags(self, tags: List[str]) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    def delete_tag(self, tag_name: str, recurse=False) -> None:
        pass

    @abc.abstractmethod
    def delete_tags(self, tag_names: List[str], recurse=False) -> None:
        pass
