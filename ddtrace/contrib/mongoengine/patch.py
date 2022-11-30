import mongoengine

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def patch():
    setattr(mongoengine, "connect", WrappedConnect(_connect))


def unpatch():
    setattr(mongoengine, "connect", _connect)
