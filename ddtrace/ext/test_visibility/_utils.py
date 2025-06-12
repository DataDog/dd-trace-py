from functools import wraps
from typing import Any
from typing import Dict
from typing import List

from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _catch_and_log_exceptions(func):
    """This decorator is meant to be used around all methods of the Test Visibility classes.

    It accepts an optional parameter to allow it to be used on functions and methods.

    No uncaught errors should ever reach the integration-side, and potentially cause crashes.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: E722
            log.error("Uncaught exception occurred while calling %s", func.__name__, exc_info=True)

    return wrapper


def _get_item_tag(item_id: TestVisibilityItemId, name: str) -> Any:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    return CIVisibility.get_item_by_id(item_id).get_tag(name)


def _set_item_tag(item_id: TestVisibilityItemId, name: str, value: Any) -> None:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    CIVisibility.get_item_by_id(item_id).set_tag(name, value)


def _set_item_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any]) -> None:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    CIVisibility.get_item_by_id(item_id).set_tags(tags)


def _delete_item_tag(item_id: TestVisibilityItemId, name: str) -> None:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    CIVisibility.get_item_by_id(item_id).delete_tag(name)


def _delete_item_tags(item_id: TestVisibilityItemId, names: List[str], recurse: bool = False) -> None:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    CIVisibility.get_item_by_id(item_id).delete_tags(names)


def _is_item_finished(item_id: TestVisibilityItemId) -> bool:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    return CIVisibility.get_item_by_id(item_id).is_finished()
