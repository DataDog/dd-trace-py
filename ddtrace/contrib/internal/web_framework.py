"""Synchronous request execution shared by web framework integrations."""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import Protocol

from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._exceptions import find_exception
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import Block_config
from ddtrace.internal.utils.http import _format_template


log = get_logger(__name__)

# Receives the request event, the callback outcome, and any exception the callback raised.
CompleteCallback = Callable[[WebFrameworkRequestEvent, Any, Optional[BaseException]], None]


class RequestInspectionDetails(NamedTuple):
    """Framework-sourced request data that subscribers receive instead of request objects."""

    peer_ip: Optional[str]
    cookies: Mapping[str, str]
    path_params: Mapping[str, Any]


class ExecuteAdapter(Protocol):
    """Inspects and may replace a callback outcome while the request context is active.

    invoke runs the application and completes its metadata before returning or re-raising.
    kwargs holds the callback's keyword arguments. An adapter that skips or replaces the
    callback owns updating the final metadata through complete.
    """

    def __call__(
        self,
        event: WebFrameworkRequestEvent,
        invoke: Callable[[], Any],
        kwargs: dict[str, Any],
        complete: CompleteCallback,
    ) -> Any: ...


def complete_request_safely(
    complete: CompleteCallback,
    event: WebFrameworkRequestEvent,
    result: Any,
    error: Optional[BaseException],
) -> None:
    """Isolate metadata failures without suppressing process-control exceptions."""
    try:
        complete(event, result, error)
    except Exception:
        log.debug("Failed to complete web framework request metadata", exc_info=True)


def run_web_framework_request(
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    prepare: Callable[[], WebFrameworkRequestEvent],
    complete: CompleteCallback,
    context_name_override: Optional[str] = None,
    execute: Optional[ExecuteAdapter] = None,
) -> Any:
    """Run a synchronous callback and complete its metadata inside a typed context.

    The optional execute adapter may inspect the callback outcome and replace it while
    the context remains active; see ExecuteAdapter for what it receives.
    Preparation failure bypasses the context and adapter, invoking the application
    once. Returned iterables are neither wrapped nor consumed.
    """
    event: Optional[WebFrameworkRequestEvent]
    try:
        event = prepare()
    except Exception:
        log.debug("Failed to prepare web framework request metadata", exc_info=True)
        event = None
    if event is None:
        return callback(*args, **kwargs)

    def invoke() -> Any:
        result = None
        error: Optional[BaseException] = None
        try:
            result = callback(*args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                complete_request_safely(complete, event, result, error)
            finally:
                # Do not retain the application's traceback through this frame.
                error = None

    with core.context_with_event(event, context_name_override=context_name_override):
        return invoke() if execute is None else execute(event, invoke, kwargs, complete)


def blocking_execute_adapter(
    *,
    request_details: Callable[[Mapping[str, Any]], RequestInspectionDetails],
    is_response_object: Callable[[Any], bool],
    extract_body: Callable[[Any], Any],
    build_response: Callable[[int, Mapping[str, str], bytes], Any],
) -> ExecuteAdapter:
    """Build an execute adapter that inspects request outcomes and serves blocked responses.

    The adapter dispatches request inspection before the callback runs and response
    inspection once the callback's metadata is complete, so subscribers can block at
    either point. A blocking exception raised anywhere in that window, including one
    nested in an exception group, is replaced by a framework response built from the
    block configuration. The outcome the replacement discards is closed without being
    iterated.

    request_details maps the callback keyword arguments to the data carried by
    WebFrameworkRequestInspectEvent. is_response_object identifies the framework's
    response type, extract_body returns that type's body, and build_response turns a
    status, headers, and body into the framework's response object.

    The returned adapter keeps no per-request state, so integrations should build it
    once rather than per request.
    """

    def execute(
        event: WebFrameworkRequestEvent,
        invoke: Callable[[], Any],
        kwargs: dict[str, Any],
        complete: CompleteCallback,
    ) -> Any:
        result = None
        error: Optional[BaseException] = None
        blocking: Optional[BlockingException] = None
        try:
            try:
                try:
                    peer_ip, cookies, path_params = request_details(kwargs)
                    # WAF evaluation can block, so it must run inside the handler rather than context entry.
                    core.dispatch_event(
                        WebFrameworkRequestInspectEvent(
                            request_event=event,
                            peer_ip=peer_ip,
                            cookies=cookies,
                            path_params=path_params,
                        )
                    )
                except BaseException as exc:
                    # The shared invocation was skipped, so it cannot complete this outcome.
                    complete_request_safely(complete, event, None, exc)
                    raise
                result = invoke()
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                core.dispatch_event(WebFrameworkResponseInspectEvent(event))
        except BaseException as exc:
            blocking = find_exception(exc, BlockingException)
            if blocking is None:
                raise
            _close_discarded_outcome(result, error, is_response_object, extract_body)
            result = _blocked_response(blocking.args[0], build_response)
            complete_request_safely(complete, event, result, None)
            return result
        finally:
            # Do not retain the application's traceback through this frame.
            error = None
            blocking = None

    return execute


def _close_discarded_outcome(
    result: Any,
    error: Optional[BaseException],
    is_response_object: Callable[[Any], bool],
    extract_body: Callable[[Any], Any],
) -> None:
    """Close the outcome a blocked response replaces, without iterating it."""
    if is_response_object(result):
        discarded_body = extract_body(result)
    elif is_response_object(error):
        discarded_body = extract_body(error)
    else:
        discarded_body = result
    close = getattr(discarded_body, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            log.debug("Failed to close the response discarded by a block", exc_info=True)


def _blocked_response(
    block: Block_config,
    build_response: Callable[[int, Mapping[str, str], bytes], Any],
) -> Any:
    """Ask subscribers for the blocked response, then build the framework's response object."""
    event = WebFrameworkBlockResponseEvent(block)
    core.dispatch_event(event)
    block_response = event.response
    if block_response is None:
        # AppSec owns the configured templates; this covers a block with no subscriber to render it.
        default_body = _format_template(BLOCKED_RESPONSE_JSON, "default").encode("utf-8")
        block_response = (
            403,
            {"Content-Type": "application/json", "Content-Length": str(len(default_body))},
            default_body,
        )
    status, headers, body = block_response
    return build_response(status, headers, body)
