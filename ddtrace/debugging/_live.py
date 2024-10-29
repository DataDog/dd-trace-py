from ddtrace.debugging._session import Session
from ddtrace.internal import core


def enable() -> None:
    core.on("distributed_context.activated", Session.activate_distributed, "live_debugger")


def disable() -> None:
    core.reset_listeners("distributed_context.activated", Session.activate_distributed)
