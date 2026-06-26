"""Per-request negative cache for SQLi RASP evaluations.

Repeated identical queries within one request skip WAF subcontext allocation
on cache hit. Only clean results are cached (no match, no timeout, no actions).

Key: (hash(sql), db_system). hash() avoids holding (potentially long) query strings in memory for
the request lifetime.

Invalidation: the cache is cleared before each main-context WAF run
(rule_type is None), since the WAF subcontext inherits main-context data and
previously-cached results may no longer hold after new request data arrives.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace.appsec._constants import WAF_DATA_NAMES


if TYPE_CHECKING:
    from ddtrace.appsec._utils import DDWaf_result


_MAX_SIZE = 128

_CacheKey = tuple[int, Optional[str]]  # (hash(sql), db_system)


class RaspSqliCache:
    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: set[_CacheKey] = set()

    def check(self, data: dict[str, Any]) -> tuple[Optional[_CacheKey], bool]:
        """Return (cache_key, is_cached).

        cache_key is None when the SQL value is unhashable (caching skipped for this call).
        """
        sql = data.get(WAF_DATA_NAMES.SQLI_ADDRESS)
        db_system = data.get(WAF_DATA_NAMES.SQLI_SYSTEM_ADDRESS)
        try:
            key: _CacheKey = (hash(sql), db_system)
            return key, key in self._cache
        except TypeError:
            return None, False

    def store(self, cache_key: _CacheKey, waf_results: "DDWaf_result") -> None:
        """Add a clean WAF result to the cache."""
        if (
            waf_results.return_code == 0
            and not waf_results.data
            and not waf_results.actions
            and not waf_results.timeout
            and not waf_results.meta_tags
            and not waf_results.metrics
            and not waf_results.api_security
            and not waf_results.keep
            and len(self._cache) < _MAX_SIZE
        ):
            self._cache.add(cache_key)

    def clear(self) -> None:
        self._cache.clear()
