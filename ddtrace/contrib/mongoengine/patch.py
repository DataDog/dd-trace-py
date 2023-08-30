import mongoengine

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def get_version():
    # type: () -> str
    return getattr(mongoengine, "__version__", "")


def patch():
    mongoengine.connect = WrappedConnect(_connect)


def unpatch():
    mongoengine.connect = _connect
