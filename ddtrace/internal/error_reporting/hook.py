import sys
import ddtrace
import traceback


def _default_datadog_exc_callback(*args):
    print("I am HOOK magic handler!!!")
    _, exc, _ = sys.exc_info()
    if not exc:
        return

    span = ddtrace.tracer.current_span()
    if not span:
        return

    span._add_event(
        "exception",
        {"message": str(exc), "type": type(exc).__name__, "stack": "".join(traceback.format_exception(exc))},
    )
