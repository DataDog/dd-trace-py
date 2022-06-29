from functools import lru_cache


class GlobMatcher:
    """This is a backtracking implementation of the glob matching algorithm.
    The glob pattern language supports `*` as a multiple character wildcard which includes matches on `""`
    and `?` as a single character wildcard, but no escape sequences.
    """

    __slots__ = "pattern"

    def __init__(self, pattern):

        self.pattern = pattern

    @lru_cache(200)
    def match(self, subject):
        # type: (str) -> bool
        pattern = self.pattern
        px = 0  # [p]attern inde[x]
        sx = 0  # [s]ubject inde[x]
        nextPx = 0
        nextSx = 0

        while px < len(pattern) or sx < len(subject):
            if px < len(pattern):
                char = pattern[px]
                if sx < len(subject) and subject[sx] == char:
                    px += 1
                    sx += 1
                    continue

                elif char == "?":  # single character wildcard
                    if sx < len(subject):
                        px += 1
                        sx += 1
                        continue

                elif char == "*":  # zero-or-more-character wildcard
                    nextPx = px
                    nextSx = sx + 1
                    px += 1
                    continue

            if 0 < nextSx and nextSx <= len(subject):
                px = nextPx
                sx = nextSx
                continue

            return False
        return True
