import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.trace import Span


log = get_logger(__name__)


def _get_item_span(item_id: t.Union[ext_api.TestVisibilityItemId, InternalTestId]) -> Span:
    return require_ci_visibility_service().get_item_by_id(item_id).get_span()
