import pylibmc

from .client import TracedClient


# Original Client class
_Client = pylibmc.Client


def get_version():
    # type: () -> str
    return getattr(pylibmc, "__version__", "")


def patch():
    pylibmc.Client = TracedClient


def unpatch():
    pylibmc.Client = _Client
