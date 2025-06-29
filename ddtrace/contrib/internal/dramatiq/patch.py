from typing import Any
from typing import Callable
from typing import Dict
from typing import Tuple

import dramatiq

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.settings._config import Config
from ddtrace.trace import tracer


def get_version() -> str:
    return str(dramatiq.__version__)


def _supported_versions() -> Dict[str, str]:
    return {"dramatiq": ">=1.10.0"}


def patch() -> None:
    """
    Instrument dramatiq so any new Actor is automatically instrumented.
    """
    if getattr(dramatiq, "__datadog_patch", False):
        return
    dramatiq.__datadog_patch = True

    trace_utils.wrap("dramatiq", "Actor.send_with_options", _traced_send_with_options_function(config.dramatiq))


def unpatch() -> None:
    """
    Disconnect remove tracing capabilities from dramatiq Actors
    """
    if not getattr(dramatiq, "__datadog_patch", False):
        return
    dramatiq.__datadog_patch = False

    trace_utils.unwrap(dramatiq.Actor, "send_with_options")


def _traced_send_with_options_function(integration_config: Config) -> Callable[[Any], Any]:
    """
    NOTE: This accounts for both the send() and send_with_options() methods,
    since send() just wraps around send_with_options() with empty options.

    In terms of expected behavior, this traces the send_with_options() calls,
    but does not reflect the actual execution time of the background task
    itself. The duration of this span is the duration of the send_with_options()
    call itself.
    """

    def _traced_send_with_options(
        func: Callable[[Any], Any], instance: dramatiq.Actor, args: Tuple[Any], kwargs: Dict[Any, Any]
    ) -> Callable[[Any], Any]:
        with tracer.trace(
            "dramatiq.Actor.send_with_options",
            span_type=SpanTypes.WORKER,
            service=trace_utils.ext_service(pin=None, int_config=integration_config),
        ) as span:
            span.set_tags(
                {
                    SPAN_KIND: SpanKind.PRODUCER,
                    "actor.name": instance.actor_name,
                    "actor.options": instance.options,
                }
            )

            return func(*args, **kwargs)

    return _traced_send_with_options
