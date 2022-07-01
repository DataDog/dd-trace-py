import random
import string

import bm

from ddtrace.internal.glob_matching import GlobMatcher


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def iter_n(items, n):
    """Return back n results from items, even if len(items) < n"""
    i = 0
    t = len(items)
    for _ in range(n):
        yield items[i]
        i += 1
        if i >= t:
            i = 0


class GlobMatchingRules(bm.Scenario):
    num_iterations = bm.var(type=int)
    num_sub = bm.var(type=int)

    def run(self):
        # Generate random strings for the counts we requested
        subjects = [rands() for _ in range(self.num_sub)]

        # Create a single pattern to use for all matches
        # Pick a random pattern
        rule = GlobMatcher(random.choice(subjects))

        def _(loops):
            for _ in range(loops):
                for subject in iter_n(subjects, n=self.num_iterations):
                    rule.match(subject)

        yield _
