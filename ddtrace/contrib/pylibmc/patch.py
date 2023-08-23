import pylibmc

from .client import TracedClient


# Original Client class
_Client = pylibmc.Client


def patch():
    pylibmc.Client = TracedClient


def unpatch():
    pylibmc.Client = _Client
