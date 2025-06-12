from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.internal.logger import get_logger
from ddtrace.trace import Span

log = get_logger(__name__)


def _get_item_span(item_id: TestVisibilityItemId) -> Span:
    log.debug("Getting span for item %s", item_id)
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import on_item_get_span
    return on_item_get_span(item_id)
