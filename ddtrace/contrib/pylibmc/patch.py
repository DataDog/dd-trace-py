import pylibmc

from .client import TracedClient


# Original Client class
_Client = pylibmc.Client


def get_version():
    return getattr(pylibmc, "__version__", "0.0.0")


def patch():
    setattr(pylibmc, "Client", TracedClient)


def unpatch():
    setattr(pylibmc, "Client", _Client)
