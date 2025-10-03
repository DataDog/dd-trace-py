import atexit
import hashlib
import json
import os
import shutil
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

_API_RESPONSE_CACHE_DIR = os.path.join(os.getcwd(), ".ddtrace_api_cache")


def _is_response_cache_enabled():
    return asbool(os.getenv("_DD_CIVISIBILITY_RESPONSE_CACHE_ENABLED", "false").lower())


def _get_cache_file_path(cache_key: str) -> str:
    """Get the full path to the cache file"""
    os.makedirs(_API_RESPONSE_CACHE_DIR, exist_ok=True)
    return os.path.join(_API_RESPONSE_CACHE_DIR, f"{cache_key}.json")


def _get_normalized_cache_key(method: str, endpoint: str, payload: t.Dict[str, t.Any]) -> str:
    """Generate a cache key by normalizing payload to remove dynamic UUID"""
    cache_data_dict = {"type": payload["data"].get("type"), "attributes": payload["data"]["attributes"]}
    # Convert to JSON string with sorted keys for consistent hashing
    normalized_payload = json.dumps(cache_data_dict, sort_keys=True)
    cache_key_data = f"{method}:{endpoint}:{normalized_payload}"
    return hashlib.sha256(cache_key_data.encode()).hexdigest()


def _read_from_cache(cache_key: str) -> t.Optional[t.Dict]:
    """Read cached response if it exists"""
    if not cache_key or not _is_response_cache_enabled():
        return None

    cache_file = _get_cache_file_path(cache_key)
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                log.debug("RESPONSE CACHE: Hit for key: %s", cache_key)
                return cached_data
    except Exception:  # noqa: E722
        log.debug("RESPONSE CACHE: Failed to read from cache for key: %s", cache_key, exc_info=True)
    return None


def _write_to_cache(cache_key: str, data: t.Any) -> None:
    """Write successful response to cache"""
    if not cache_key or not _is_response_cache_enabled():
        return
    cache_file = _get_cache_file_path(cache_key)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
            log.debug("RESPONSE CACHE: Wrote response for key: %s", cache_key)
    except Exception:  # noqa: E722
        log.debug("RESPONSE CACHE: Failed to write to cache for key: %s", cache_key, exc_info=True)


def _clean_api_response_cache_dir():
    if os.path.exists(_API_RESPONSE_CACHE_DIR):
        shutil.rmtree(_API_RESPONSE_CACHE_DIR)


if os.environ.get("PYTEST_XDIST_WORKER") is None:  # Not an xdist worker
    atexit.register(_clean_api_response_cache_dir)
