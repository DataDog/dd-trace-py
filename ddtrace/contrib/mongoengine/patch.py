import mongoengine

from ...internal.utils.deprecation import deprecated
from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def patch():
    setattr(mongoengine, "connect", WrappedConnect(_connect))


def unpatch():
    setattr(mongoengine, "connect", _connect)


@deprecated(message="Use patching instead (see the docs).", version="1.0.0")
def trace_mongoengine(*args, **kwargs):
    return _connect
