"""IAST request ownership for integrations that emit explicit lifecycle events.

ASGI can finish the response span in a child task. Completion binds the saved
request context and native taint identifier so reporting uses the right request.
Expired identifiers left in other tasks cannot access a later request's native
state. Keep the completed owner on its context so late child spans cannot start
another analysis; the existing context lifetime releases this bookkeeping.
"""

from dataclasses import dataclass
from typing import Optional
from weakref import WeakKeyDictionary

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._iast_request_context import _iast_end_request
from ddtrace.appsec._iast._iast_request_context_base import _discard_expired_iast_env
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._taint_tracking._context import get_tainted_object_map_by_ctx_id
from ddtrace.appsec._iast_context import IAST_CONTEXT
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config


_REQUEST_STATE = "iast.request_lifecycle"
# Remove the migration policy with the legacy processor. Enable entries with their hooks.
_EVENT_MANAGED_REQUESTS: frozenset[tuple[str, str]] = frozenset({("asgi.request", "fastapi")})
# Temporary coexistence bookkeeping; remove with the legacy processor.
_SPAN_ROUTES: WeakKeyDictionary[SpanData, bool] = WeakKeyDictionary()


@dataclass
class _RequestState:
    """A completed marker also reserves admission for a borrowed outer request."""

    span: SpanData
    context_id: Optional[int]
    completed: bool = False


def find_request_owner(ctx: core.ExecutionContext[Event]) -> Optional[core.ExecutionContext[Event]]:
    # Rejected requests also have an owner: nested wrappers must not resample them.
    current: Optional[core.ExecutionContext[Event]] = ctx
    new_request = False
    while current is not None:
        if (state := current.get_item(_REQUEST_STATE)) is not None:
            if new_request:
                context_id = IAST_CONTEXT.get()
                if (
                    context_id is None
                    or context_id != state.context_id
                    or not get_tainted_object_map_by_ctx_id(context_id)
                ):
                    return None
            return current
        # Independent requests share live analysis, as legacy admission does.
        # After it ends they can start afresh; mounted ASGI wrappers still share the
        # original request's decision even when it was rejected or completed.
        if current.identifier == "asgi.request" and current.get_item("is_subapp") is False:
            new_request = True
        elif current.identifier != "asgi.request" and current.get_item("span_type") == SpanTypes.WEB:
            new_request = True
        current = current.parent
    return None


def _is_event_managed_request(ctx: core.ExecutionContext[Event]) -> bool:
    if ctx.identifier != "asgi.request":
        return False
    integration_config = ctx.get_item("integration_config")
    return (
        integration_config is not None
        and (ctx.identifier, integration_config.integration_name) in _EVENT_MANAGED_REQUESTS
    )


def uses_request_events(ctx: core.ExecutionContext[Event]) -> bool:
    return (
        _is_event_managed_request(ctx)
        and core.has_listeners(WebRequestReady.event_name)
        and core.has_listeners(WebRequestCompleting.event_name)
    ) or find_request_owner(ctx) is not None


def _has_legacy_ancestor(span: SpanData) -> bool:
    parent = span._parent
    while parent is not None:
        if _SPAN_ROUTES.get(parent) is False:
            return True
        parent = parent._parent
    return False


def on_request_ready(event: WebRequestReady) -> None:
    if not asm_config._iast_enabled or not _is_event_managed_request(event.ctx) or find_request_owner(event.ctx):
        return
    with core.activate_context(event.ctx):
        state = _RequestState(event.span, None)
        event.ctx.set_item(_REQUEST_STATE, state)
        # Preserve live outer analysis, but let admission replace an inherited
        # expired identifier and its environment.
        context_id = IAST_CONTEXT.get()
        if context_id is not None and get_tainted_object_map_by_ctx_id(context_id):
            state.context_id = context_id
            state.completed = True
            return
        environment = _get_iast_env()
        if environment is not None and environment.context_id is None:
            state.completed = True
            return
        if environment is not None and environment.context_id is not None:
            if get_tainted_object_map_by_ctx_id(environment.context_id):
                # A shared ancestor can expose another task's environment even
                # though this execution never inherited its native identifier.
                event.ctx.set_item(IAST.REQUEST_CONTEXT_KEY, None)
            else:
                _discard_expired_iast_env(environment.context_id)
        # A declined legacy request still owns admission. An inner wrapper must
        # not retry after capacity or sampling changes during middleware awaits.
        if event.ctx.get_item("is_subapp") is not False and _has_legacy_ancestor(event.span):
            state.completed = True
            return
        try:
            _iast_start_request(event.span)
            # Admission can return an expired id that it just cleared on rejection.
            state.context_id = IAST_CONTEXT.get()
        except BaseException:
            # Admission can allocate its native map before environment creation fails.
            state.context_id = IAST_CONTEXT.get()
            _iast_finish_request(event.span)
            state.completed = True
            raise


def on_request_completing(event: WebRequestCompleting) -> None:
    ctx, span = event.ctx, event.span
    state = ctx.get_item(_REQUEST_STATE)
    if state is None or state.span is not span or state.completed:
        return
    state.completed = True
    previous_context_id = IAST_CONTEXT.get()
    token = IAST_CONTEXT.set(state.context_id)
    try:
        with core.activate_context(ctx):
            try:
                _iast_end_request(span)
            finally:
                # Reporting can fail, or IAST can be disabled after admission. Neither
                # may strand the environment or its native taint map.
                if _get_iast_env() is not None or IAST_CONTEXT.get() is not None:
                    _iast_finish_request(span)
    finally:
        # Completed analysis must not expose an unrelated ancestor environment
        # to middleware or background work that still holds this request scope.
        ctx.set_item(IAST.REQUEST_CONTEXT_KEY, None)
        # A callback may run while another request is active. Restore that request,
        # but never restore this request's identifier after freeing its taint map.
        if previous_context_id != state.context_id:
            IAST_CONTEXT.reset(token)
