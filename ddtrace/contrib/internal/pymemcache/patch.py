import pymemcache
import pymemcache.client.hash

from .client import WrappedClient
from .client import WrappedHashClient


_Client = pymemcache.client.base.Client
_hash_Client = pymemcache.client.hash.Client
_hash_HashClient = pymemcache.client.hash.Client


def get_version() -> str:
    return getattr(pymemcache, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"pymemcache": ">=3.4"}


def patch():
    if getattr(pymemcache, "_datadog_patch", False):
        return

    pymemcache._datadog_patch = True
    pymemcache.client.base.Client = WrappedClient
    pymemcache.client.hash.Client = WrappedClient
    pymemcache.client.hash.HashClient = WrappedHashClient


def unpatch():
    """Remove pymemcache tracing"""
    if not getattr(pymemcache, "_datadog_patch", False):
        return
    pymemcache._datadog_patch = False
    pymemcache.client.base.Client = _Client
    pymemcache.client.hash.Client = _hash_Client
    pymemcache.client.hash.HashClient = _hash_HashClient
