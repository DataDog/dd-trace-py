try:
    from ._stack_v2 import *  # noqa: F401, F403

    def is_available():
        # type: () -> bool
        return True

except Exception as e:
    from ddtrace.internal.logger import get_logger

    LOG = get_logger(__name__)
    LOG.warning("Failed to import _stack_v2: %s", e)

    def is_available():
        # type: () -> bool
        return False

    # Decorator for not-implemented
    def not_implemented(func):
        def wrapper(*args, **kwargs):
            raise NotImplementedError("{} is not implemented on this platform".format(func.__name__))

    @not_implemented
    def start(*args, **kwargs):
        pass

    @not_implemented
    def stop(*args, **kwargs):
        pass

    @not_implemented
    def set_interval(*args, **kwargs):
        pass
