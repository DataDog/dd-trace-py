import random
import re
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
    re_matcher = bm.var(type=bool)

    def run(self):
        # Generate random strings for the counts we requested
        subjects = [rands() for _ in range(self.num_sub)]

        # Create a single pattern to use for all matches
        # Pick a random pattern
        pattern = random.choice(subjects)
        rule = GlobMatcher(pattern)

        # Regular expression function to test against
        def re_match(self, sub):
            if re.match(pattern, sub):
                return True

        def _(loops):
            # Test the regular expression matching if that's the scenario specified
            if self.re_matcher:
                for _ in range(loops):
                    for subject in iter_n(subjects, n=self.num_iterations):
                        re_match(subject)
            # Test the custom glob matchng if that's the scenario specified
            else:
                for _ in range(loops):
                    for subject in iter_n(subjects, n=self.num_iterations):
                        rule.match(subject)

        yield _
