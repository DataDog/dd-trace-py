import pylibmc

from .client import TracedClient

# Original Client class
_Client = pylibmc.Client


def patch():
    setattr(pylibmc, 'Client', TracedClient)

def unpatch():
    setattr(pylibmc, 'Elasticsearch', _Client)

