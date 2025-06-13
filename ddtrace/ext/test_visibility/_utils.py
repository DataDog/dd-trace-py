from typing import Any
from typing import Dict
from typing import List

from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service


def _get_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
    return require_ci_visibility_service().get_item_by_id(item_id).get_tag(tag_name)


def _set_item_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).set_tag(tag_name, tag_value)


def _set_item_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any]) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).set_tags(tags)


def _delete_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).delete_tag(tag_name)


def _delete_item_tags(item_id: TestVisibilityItemId, tag_names: List[str]) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).delete_tags(tag_names)


def _is_item_finished(item_id: TestVisibilityItemId) -> bool:
    return require_ci_visibility_service().get_item_by_id(item_id).is_finished()
