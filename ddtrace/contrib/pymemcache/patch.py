import pymemcache
import pymemcache.client.hash

from ddtrace.ext import memcached as memcachedx
from ddtrace.pin import Pin
from ddtrace.pin import _DD_PIN_NAME
from ddtrace.pin import _DD_PIN_PROXY_NAME

from .client import WrappedClient


_Client = pymemcache.client.base.Client
_hash_Client = pymemcache.client.hash.Client

_HashClient_client_class = None
if hasattr(pymemcache.client.hash.HashClient, "client_class"):
    _HashClient_client_class = pymemcache.client.hash.HashClient.client_class


def patch():
    if getattr(pymemcache.client, "_datadog_patch", False):
        return

    setattr(pymemcache.client, "_datadog_patch", True)
    setattr(pymemcache.client.base, "Client", WrappedClient)
    setattr(pymemcache.client.hash, "Client", WrappedClient)
    if _HashClient_client_class:
        pymemcache.client.hash.HashClient.client_class = WrappedClient

    # Create a global pin with default configuration for our pymemcache clients
    Pin(app=memcachedx.SERVICE, service=memcachedx.SERVICE).onto(pymemcache)


def unpatch():
    """Remove pymemcache tracing"""
    if not getattr(pymemcache.client, "_datadog_patch", False):
        return
    setattr(pymemcache.client, "_datadog_patch", False)
    setattr(pymemcache.client.base, "Client", _Client)
    setattr(pymemcache.client.hash, "Client", _hash_Client)
    if _HashClient_client_class:
        pymemcache.client.hash.HashClient.client_class = _HashClient_client_class

    # Remove any pins that may exist on the pymemcache reference
    setattr(pymemcache, _DD_PIN_NAME, None)
    setattr(pymemcache, _DD_PIN_PROXY_NAME, None)
