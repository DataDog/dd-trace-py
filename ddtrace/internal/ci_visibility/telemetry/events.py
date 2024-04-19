from enum import Enum
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE
from ddtrace.internal.ci_visibility.telemetry.constants import EVENT_TYPES
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)


class EVENTS_TELEMETRY(str, Enum):
    CREATED = "event_created"
    FINISHED = "event_finished"
    MANUAL_API_EVENT = "manual_api_event"
    ENQUEUED_FOR_SERIALIZATION = "events_enqueued_for_serialization"


def _record_event(
    event: EVENTS_TELEMETRY,
    event_type: EVENT_TYPES,
    test_framework: Optional[TEST_FRAMEWORKS],
    has_codeowners: bool = False,
    unsupported_ci: bool = False,
    is_benchmark: bool = False,
):
    if has_codeowners and event_type != EVENT_TYPES.SESSION:
        log.debug("has_codeowners tag can only be set for sessions, but event type is %s", event_type)
    if unsupported_ci and event_type != EVENT_TYPES.SESSION:
        log.debug("unsupported_ci tag can only be set for sessions, but event type is %s", event_type)
    if is_benchmark and event_type != EVENT_TYPES.TEST:
        log.debug("is_benchmark tag can only be set for tests, but event type is %s", event_type)

    _tags: List[Tuple[str, str]] = [("event_type", event_type)]
    if test_framework and test_framework != TEST_FRAMEWORKS.MANUAL:
        _tags.append(("test_framework", test_framework))
    if event_type == EVENT_TYPES.SESSION:
        if has_codeowners:
            _tags.append(("has_codeowners", "1"))
        if unsupported_ci:
            _tags.append(("is_unsupported_ci", "1"))
    if event_type == EVENT_TYPES.TEST and is_benchmark:
        _tags.append(("is_benchmark", "1"))

    telemetry_writer.add_count_metric(_NAMESPACE, event, 1, tuple(_tags))


def record_event_created(
    event_type: EVENT_TYPES,
    test_framework: TEST_FRAMEWORKS,
    has_codeowners: bool = False,
    unsupported_ci: bool = False,
    is_benchmark: bool = False,
):
    if test_framework == TEST_FRAMEWORKS.MANUAL:
        # manual API usage is tracked only by way of tracking created events
        record_manual_api_event_created(event_type)
    _record_event(
        event=EVENTS_TELEMETRY.CREATED,
        event_type=event_type,
        test_framework=test_framework,
        has_codeowners=has_codeowners,
        unsupported_ci=unsupported_ci,
        is_benchmark=is_benchmark,
    )


def record_event_finished(
    event_type: EVENT_TYPES,
    test_framework: Optional[TEST_FRAMEWORKS],
    has_codeowners: bool = False,
    unsupported_ci: bool = False,
    is_benchmark: bool = False,
):
    _record_event(
        event=EVENTS_TELEMETRY.FINISHED,
        event_type=event_type,
        test_framework=test_framework,
        has_codeowners=has_codeowners,
        unsupported_ci=unsupported_ci,
        is_benchmark=is_benchmark,
    )


def record_manual_api_event_created(event_type: EVENT_TYPES):
    # Note: _created suffix is added in cases we were to change the metric name in the future.
    # The current metric applies to event creation even though it does not specify it
    telemetry_writer.add_count_metric(_NAMESPACE, EVENTS_TELEMETRY.MANUAL_API_EVENT, 1, (("event_type", event_type),))


def record_events_enqueued_for_serialization(events_count: int):
    telemetry_writer.add_count_metric(_NAMESPACE, EVENTS_TELEMETRY.ENQUEUED_FOR_SERIALIZATION, events_count)
