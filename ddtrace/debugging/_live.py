import typing as t

from ddtrace.debugging._session import Session
from ddtrace.internal import core


def handle_distributed_context(context: t.Any) -> None:
    debug_tag = context._meta.get("_dd.p.debug")
    if debug_tag is None:
        return

    for session in debug_tag.split(","):
        ident, _, level = session.partition(":")
        Session(ident=ident, level=int(level or 0)).link_to_trace(context)


def enable() -> None:
    core.on("distributed_context.activated", handle_distributed_context, "live_debugger")


def disable() -> None:
    core.reset_listeners("distributed_context.activated", handle_distributed_context)
