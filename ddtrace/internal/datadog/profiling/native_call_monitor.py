"""
Native C function call tracking using sys.monitoring (Python 3.12+).

Uses a C-level sys.monitoring CALL handler that registers each call site
in a static registry keyed by (code_object, lasti), then returns DISABLE
for every callable (Python and C). After warmup (one fire per call site),
overhead drops to zero. The sampler looks up (code_object, lasti) when
reading frames to inject native call information.
"""

# Protected by the GIL -- start() and stop() are only called from Python code
# holding the GIL, so concurrent access is not possible.
_started = False


def start():
    """Start sys.monitoring-based native call tracking from C."""
    global _started
    if _started:
        return

    from ddtrace.internal.datadog.profiling.stack._stack import start_native_monitoring

    start_native_monitoring()
    _started = True


def stop():
    """Stop sys.monitoring-based native call tracking."""
    global _started
    if not _started:
        return

    from ddtrace.internal.datadog.profiling.stack._stack import stop_native_monitoring

    try:
        stop_native_monitoring()
    finally:
        _started = False
