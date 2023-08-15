import mongoengine

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def get_version():
    return getattr(mongoengine, "__version__", "0.0.0")


def patch():
    setattr(mongoengine, "connect", WrappedConnect(_connect))


def unpatch():
    setattr(mongoengine, "connect", _connect)
