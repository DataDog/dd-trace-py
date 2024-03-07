is_available = False


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


try:
    from ._stack_v2 import *  # noqa: F401, F403

    is_available = True
except Exception as e:
    from ddtrace.internal.logger import get_logger

    LOG = get_logger(__name__)

    LOG.debug("Failed to import _stack_v2: %s", e)
