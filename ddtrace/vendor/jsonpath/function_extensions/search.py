"""The standard `search` function extension."""

from ._pattern import AbstractRegexFilterFunction


class Search(AbstractRegexFilterFunction):
    """The standard `search` function."""

    def __call__(self, value: object, pattern: object) -> bool:
        """Return `True` if _value_ matches _pattern_, or `False` otherwise."""
        if not isinstance(value, str) or not isinstance(pattern, str):
            return False

        _pattern = self.check_cache(pattern)

        if _pattern is None:
            return False

        return bool(_pattern.search(value))
