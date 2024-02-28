try:
    from ddtrace.internal.datadog.profiling.stack_v2 import *  # noqa: F401, F403

    def is_available():
        return True

except Exception:

    def is_available():
        return False

    def start(*args, **kwargs):
        pass

    def stop(*args, **kwargs):
        pass

    def set_interval(*args, **kwargs):
        pass
