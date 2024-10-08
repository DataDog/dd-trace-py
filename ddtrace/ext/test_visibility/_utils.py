from functools import wraps
from typing import Any
from typing import Dict
from typing import List

from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.ext.test_visibility._test_visibility_base import _TestVisibilityAPIBase
from ddtrace.internal import core
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


def _get_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
    log.debug("Getting tag for item %s: %s", item_id, tag_name)
    tag_value = core.dispatch_with_results(
        "test_visibility.item.get_tag", (_TestVisibilityAPIBase.GetTagArgs(item_id, tag_name),)
    ).tag_value.value
    return tag_value


def _set_item_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any, recurse: bool = False):
    log.debug("Setting tag for item %s: %s=%s", item_id, tag_name, tag_value)
    core.dispatch("test_visibility.item.set_tag", (_TestVisibilityAPIBase.SetTagArgs(item_id, tag_name, tag_value),))


def _set_item_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any], recurse: bool = False):
    log.debug("Setting tags for item %s: %s", item_id, tags)
    core.dispatch("test_visibility.item.set_tags", (_TestVisibilityAPIBase.SetTagsArgs(item_id, tags),))


def _delete_item_tag(item_id: TestVisibilityItemId, tag_name: str, recurse: bool = False):
    log.debug("Deleting tag for item %s: %s", item_id, tag_name)
    core.dispatch("test_visibility.item.delete_tag", (_TestVisibilityAPIBase.DeleteTagArgs(item_id, tag_name),))


def _delete_item_tags(item_id: TestVisibilityItemId, tag_names: List[str], recurse: bool = False):
    log.debug("Deleting tags for item %s: %s", item_id, tag_names)
    core.dispatch("test_visibility.item.delete_tags", (_TestVisibilityAPIBase.DeleteTagsArgs(item_id, tag_names),))


def _is_item_finished(item_id: TestVisibilityItemId) -> bool:
    log.debug("Checking if item %s is finished", item_id)
    _is_finished = bool(core.dispatch_with_results("test_visibility.item.is_finished", (item_id,)).is_finished.value)
    log.debug("Item %s is finished: %s", item_id, _is_finished)
    return _is_finished
