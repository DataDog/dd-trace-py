"""AppSec HTTP inspection scaffold with placeholder evaluation and reporting hooks."""

from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpDecisionAcceptance
from ddtrace.contrib._events.http import HttpEnforcementAction
from ddtrace.contrib._events.http import HttpInspectionDecision
from ddtrace.contrib._events.http import HttpRequestInspectionEvent
from ddtrace.contrib._events.http import HttpRequestSnapshot
from ddtrace.internal.core.subscriber import Subscriber


def should_block(snapshot: HttpRequestSnapshot) -> bool:
    """Placeholder for AppSec-owned evaluation; no requests are blocked yet."""
    return False


def record_acceptance(request_id: str, acceptance: HttpDecisionAcceptance) -> None:
    """Placeholder for recording the integration's acceptance of a decision."""
    pass


class AppSecHttpSubscriber(Subscriber):
    auto_register = False
    event_names = (HttpRequestInspectionEvent.event_name,)

    @classmethod
    def on_event(cls, event: HttpRequestInspectionEvent) -> None:
        cls.on_inspect(event)

    @classmethod
    def on_inspect(cls, event: HttpRequestInspectionEvent) -> None:
        if HttpEnforcementAction.RESPOND not in event.supported_actions:
            return

        # Placeholder for AppSec-owned evaluation and response selection.
        if not should_block(event.snapshot):
            return

        acceptance = event.submit(
            HttpInspectionDecision(
                action=HttpEnforcementAction.RESPOND,
                response=HttpBlockingResponse(status_code=403),
            )
        )
        record_acceptance(event.snapshot.request_id, acceptance)
