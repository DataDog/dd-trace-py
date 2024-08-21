from functools import wraps
from typing import Any
from typing import Dict
from typing import List

from ddtrace import Span
from ddtrace.ext.ci_visibility._ci_visibility_base import CIItemId
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _catch_and_log_exceptions(func):
    """This decorator is meant to be used around all methods of the CIVisibility classes.

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


def _get_item_tag(item_id: CIItemId, tag_name: str) -> Any:
    log.debug("Getting tag for item %s: %s", item_id, tag_name)
    tag_value = core.dispatch_with_results(
        "ci_visibility.item.get_tag", (_CIVisibilityAPIBase.GetTagArgs(item_id, tag_name),)
    ).tag_value.value
    return tag_value


def _set_item_tag(item_id: CIItemId, tag_name: str, tag_value: Any, recurse: bool = False):
    log.debug("Setting tag for item %s: %s=%s", item_id, tag_name, tag_value)
    core.dispatch("ci_visibility.item.set_tag", (_CIVisibilityAPIBase.SetTagArgs(item_id, tag_name, tag_value),))


def _set_item_tags(item_id: CIItemId, tags: Dict[str, Any], recurse: bool = False):
    log.debug("Setting tags for item %s: %s", item_id, tags)
    core.dispatch("ci_visibility.item.set_tags", (_CIVisibilityAPIBase.SetTagsArgs(item_id, tags),))


def _delete_item_tag(item_id: CIItemId, tag_name: str, recurse: bool = False):
    log.debug("Deleting tag for item %s: %s", item_id, tag_name)
    core.dispatch("ci_visibility.item.delete_tag", (_CIVisibilityAPIBase.DeleteTagArgs(item_id, tag_name),))


def _delete_item_tags(item_id: CIItemId, tag_names: List[str], recurse: bool = False):
    log.debug("Deleting tags for item %s: %s", item_id, tag_names)
    core.dispatch("ci_visibility.item.delete_tags", (_CIVisibilityAPIBase.DeleteTagsArgs(item_id, tag_names),))


def _get_item_span(item_id: CIItemId) -> Span:
    log.debug("Getting span for item %s", item_id)
    span: Span = core.dispatch_with_results("ci_visibility.item.get_span", (item_id,)).span.value
    return span


def _is_item_finished(item_id: CIItemId) -> bool:
    log.debug("Checking if item %s is finished", item_id)
    _is_finished = bool(core.dispatch_with_results("ci_visibility.item.is_finished", (item_id,)).is_finished.value)
    log.debug("Item %s is finished: %s", item_id, _is_finished)
    return _is_finished
