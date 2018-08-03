import pymemcache

from .client import WrappedClient

_Client = pymemcache.client.base.Client


def patch():
    if getattr(pymemcache.client, "_datadog_patch", False):
        return

    setattr(pymemcache.client, "_datadog_patch", True)
    setattr(pymemcache.client.base, "Client", WrappedClient)


def unpatch():
    """Remove pymemcache tracing"""
    if not getattr(pymemcache.client, "_datadog_patch", False):
        return
    setattr(pymemcache.client, "_datadog_patch", False)
    setattr(pymemcache.client.base, "Client", _Client)
