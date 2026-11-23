"""Activation of propagated tracing contexts for instrumented requests."""

from typing import MutableMapping
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal.core.event_hub import dispatch
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Tracer


log = get_logger(__name__)


def distributed_tracing_enabled(int_config: IntegrationConfig, default: bool = False) -> bool:
    """Returns whether distributed tracing is enabled for this integration config"""
    # IntegrationConfig stores dynamically typed settings.
    if "distributed_tracing_enabled" in int_config and int_config.distributed_tracing_enabled is not None:
        return int_config.distributed_tracing_enabled  # type: ignore[no-any-return]
    elif "distributed_tracing" in int_config and int_config.distributed_tracing is not None:
        return int_config.distributed_tracing  # type: ignore[no-any-return]
    return default


def activate_distributed_headers(
    tracer: Tracer,
    int_config: Optional[IntegrationConfig] = None,
    request_headers: Optional[MutableMapping[str, str]] = None,
    override: Optional[bool] = None,
) -> None:
    """
    Helper for activating a distributed trace headers' context if enabled in integration config.
    int_config will be used to check if distributed trace headers context will be activated, but
    override will override whatever value is set in int_config if passed any value other than None.
    """
    if override is False:
        return None

    if override or (int_config and distributed_tracing_enabled(int_config)):
        context = HTTPPropagator.extract(request_headers)  # type: ignore[no-untyped-call]
        # bail out if no context was extracted
        if context is None:
            return None
        # Only need to activate the new context if something was propagated
        # The new context must have one of these values in order for it to be activated
        if not context.trace_id and not context._baggage and not context._span_links:
            return None
        # Do not reactivate a context with the same trace id
        # DEV: An example could be nested web frameworks, when one layer already
        #      parsed request headers and activated them.
        #
        # Example::
        #
        #     app = Flask(__name__)  # Traced via Flask instrumentation
        #     app = DDWSGIMiddleware(app)  # Extra layer on top for WSGI
        current_context = tracer.current_trace_context()

        # We accept incoming contexts with only baggage or only span_links, however if we
        # already have a current_context then an incoming context not
        # containing a trace_id or containing the same trace_id
        # should not be activated.
        if current_context and (
            not context.trace_id or (context.trace_id and context.trace_id == current_context.trace_id)
        ):
            log.debug(
                "will not activate extracted Context(trace_id=%r, span_id=%r), a context with that trace id is already active",  # noqa: E501
                context.trace_id,
                context.span_id,
            )
            return None

        # We have parsed a trace id from headers, and we do not already
        # have a context with the same trace id active
        tracer.context_provider.activate(context)
        core.dispatch("http.activate_distributed_headers", (request_headers, context))

        dispatch("distributed_context.activated", (context,))
