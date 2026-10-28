from typing import List
from typing import Optional

try:
    import regex as re

    REGEX_AVAILABLE = True
except ImportError:
    import re  # type: ignore

    REGEX_AVAILABLE = False

try:
    from iregexp_check import check

    IREGEXP_AVAILABLE = True
except ImportError:
    IREGEXP_AVAILABLE = False

from ..exceptions import JSONPathError
from ..function_extensions import ExpressionType
from ..function_extensions import FilterFunction
from ..lru_cache import LRUCache
from ..lru_cache import ThreadSafeLRUCache


class AbstractRegexFilterFunction(FilterFunction):
    """Base class for filter function that accept regular expression arguments.

    Arguments:
        cache_capacity: The size of the regular expression cache.
        debug: When `True`, raise an exception when regex pattern compilation
            fails. The default - as required by RFC 9535 - is `False`, which
            silently ignores bad patterns.
        thread_safe: When `True`, use a `ThreadSafeLRUCache` instead of an
            instance of `LRUCache`.
    """

    arg_types = [ExpressionType.VALUE, ExpressionType.VALUE]
    return_type = ExpressionType.LOGICAL

    def __init__(
        self,
        *,
        cache_capacity: int = 300,
        debug: bool = False,
        thread_safe: bool = False,
    ):
        self.cache: LRUCache[str, Optional[re.Pattern]] = (  # type: ignore
            ThreadSafeLRUCache(capacity=cache_capacity)
            if thread_safe
            else LRUCache(capacity=cache_capacity)
        )

        self.debug = debug

    def check_cache(self, pattern: str) -> Optional[re.Pattern]:  # type: ignore
        """Return a compiled re pattern if `pattern` is valid, or `None` otherwise."""
        try:
            _pattern = self.cache[pattern]
        except KeyError:
            if IREGEXP_AVAILABLE and not check(pattern):
                if self.debug:
                    raise JSONPathError(
                        "search pattern is not a valid I-Regexp", token=None
                    ) from None
                _pattern = None
            else:
                if REGEX_AVAILABLE:
                    pattern = map_re(pattern)

                try:
                    _pattern = re.compile(pattern)
                except re.error:
                    if self.debug:
                        raise
                    _pattern = None

            self.cache[pattern] = _pattern

        return _pattern


def map_re(pattern: str) -> str:
    """Convert an I-Regexp pattern into a Python re pattern."""
    escaped = False
    char_class = False
    parts: List[str] = []
    for ch in pattern:
        if escaped:
            parts.append(ch)
            escaped = False
            continue

        if ch == ".":
            if not char_class:
                parts.append(r"(?:(?![\r\n])\P{Cs}|\p{Cs}\p{Cs})")
            else:
                parts.append(ch)
        elif ch == "\\":
            escaped = True
            parts.append(ch)
        elif ch == "[":
            char_class = True
            parts.append(ch)
        elif ch == "]":
            char_class = False
            parts.append(ch)
        else:
            parts.append(ch)

    return "".join(parts)
