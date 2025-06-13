from typing import Any
from typing import Dict
from typing import List

from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId


def _get_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    return CIVisibility.get_item_by_id(item_id).get_tag(tag_name)


def _set_item_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any) -> None:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    CIVisibility.get_item_by_id(item_id).set_tag(tag_name, tag_value)


def _set_item_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any]) -> None:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    CIVisibility.get_item_by_id(item_id).set_tags(tags)


def _delete_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> None:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    CIVisibility.get_item_by_id(item_id).delete_tag(tag_name)


def _delete_item_tags(item_id: TestVisibilityItemId, tag_names: List[str]) -> None:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    CIVisibility.get_item_by_id(item_id).delete_tags(tag_names)


def _is_item_finished(item_id: TestVisibilityItemId) -> bool:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility
    return CIVisibility.get_item_by_id(item_id).is_finished()
