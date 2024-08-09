import mongoengine

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def get_version():
    # type: () -> str
    return getattr(mongoengine, "__version__", "")


def patch():
    mongoengine.connect = WrappedConnect(_connect)
    mongoengine._datadog_patch = True


def unpatch():
    mongoengine.connect = _connect
    mongoengine._datadog_patch = False
