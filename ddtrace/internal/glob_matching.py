class GlobMatcher:
    __slots__ = "pattern"

    def __init__(self, pattern):

        self.pattern = pattern

    def match(self, subject):
        # type: (str) -> bool
        pattern = self.pattern
        px = 0
        sx = 0
        nextPx = 0
        nextSx = 0

        while px < len(pattern) or sx < len(subject):
            if px < len(pattern):
                char = pattern[px]
                if sx < len(subject) and subject[sx] == char:
                    px += 1
                    sx += 1
                    continue

                elif char == "?":
                    if sx < len(subject):
                        px += 1
                        sx += 1
                        continue

                elif char == "*":
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
