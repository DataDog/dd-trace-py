import mongoengine

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def patch():
    mongoengine.connect = WrappedConnect(_connect)


def unpatch():
    mongoengine.connect = _connect
