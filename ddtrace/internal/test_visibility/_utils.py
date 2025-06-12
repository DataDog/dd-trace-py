import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.trace import Span


log = get_logger(__name__)


def _get_item_span(item_id: t.Union[ext_api.TestVisibilityItemId, InternalTestId]) -> Span:
    # Lazy import to avoid circular dependency
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    return CIVisibility.get_item_by_id(item_id).get_span()
