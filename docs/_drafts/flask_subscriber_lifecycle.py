"""Illustrative Flask lifecycle wiring; production also retains security callbacks.

The integration signals completion. SpanLifecycleSubscriber owns span creation,
product lifecycle notifications, and finishing. Importing this draft does not
register any subscribers.
"""

from collections.abc import Callable
from collections.abc import Iterable
from contextvars import copy_context
import sys

from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.flask._streaming import FlaskResponseIterable
from ddtrace.internal import core
from ddtrace.internal.products import SubscriberBundle


RequestScope = core.ExecutionContext[WebFrameworkRequestEvent]


def run_flask_request(
    event: WebFrameworkRequestEvent,
    execute: Callable[[RequestScope], Iterable[bytes]],
) -> Iterable[bytes]:
    execution_context = copy_context()

    def invoke() -> Iterable[bytes]:
        with core.context_with_event(event, context_name_override="flask.request", dispatch_end_event=False) as ctx:
            try:
                response = execute(ctx)
            except BaseException:
                ctx.dispatch_ended_event(*sys.exc_info())
                raise
            streamed_response = FlaskResponseIterable(response, ctx, execution_context)
        return streamed_response

    return execution_context.run(invoke)


# Production FlaskRequestSubscriber extends SpanLifecycleSubscriber, retains the
# existing Flask metadata callbacks, and finishes the response child before root.
# TODO: Complete validation of response-child completion and stream/close error
# attribution across the supported Flask/security matrix before declaring parity.
FLASK_REQUEST_SUBSCRIBERS = SubscriberBundle(
    module="ddtrace._trace.subscribers.flask",
    subscribers=("FlaskRequestSubscriber",),
)


def register_subscribers() -> None:
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    FLASK_REQUEST_SUBSCRIBERS.register()


def unregister_subscribers() -> None:
    # Keep tracing completion listeners for active and manually instrumented work.
    pass
