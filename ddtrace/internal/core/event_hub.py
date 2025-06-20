from ddtrace.internal.native._native import event_hub as _native_event_hub


# Export native functions and classes
has_listeners = _native_event_hub.has_listeners
on = _native_event_hub.on
on_all = _native_event_hub.on_all
dispatch = _native_event_hub.dispatch
reset = _native_event_hub.reset
dispatch_with_results = _native_event_hub.dispatch_with_results
EventResult = _native_event_hub.EventResult
EventResultDict = _native_event_hub.EventResultDict
ResultType = _native_event_hub.ResultType

__all__ = [
    "has_listeners",
    "on",
    "on_all",
    "reset",
    "dispatch",
    "dispatch_with_results",
    "EventResult",
    "EventResultDict",
    "ResultType",
]
