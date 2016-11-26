import mongoengine

from .trace import WrappedConnect
from ddtrace.util import deprecated

# Original connect function
_connect = mongoengine.connect


def patch():
    setattr(mongoengine, 'connect', WrappedConnect(_connect))

def unpatch():
    setattr(mongoengine, 'connect', _connect)

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def trace_mongoengine(*args, **kwargs):
    return _connect

