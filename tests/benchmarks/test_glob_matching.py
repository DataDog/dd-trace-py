import random
import re
import string

import pytest

from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.utils.cache import cachedmethod


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


@pytest.mark.parametrize(
    "num_subjects",
    [
        (1),  # Every iteration should match, high cache hit rate
        (2),  # Low number of variations, hit rate of about 50%
        (50),  # High number of variations, hit rate of about 2%
        (100),  # High number of variations, hit rate of about 1%
    ],
)
def test_glob_matching(benchmark, num_subjects):
    # Generate random strings for the counts we requested
    subjects = [rands() for _ in range(num_subjects)]

    # Create a single pattern to use for all matches
    # Pick a random pattern
    pattern = random.choice(subjects)
    rule = GlobMatcher(pattern)

    def func():
        for subject in subjects:
            rule.match(subject)

    benchmark(func)


# This comparison is without all of the character replacing we'd have to do
# in order to  make the regex expression be evaluated as a glob expression.
# Still the glob match is faster, if the glob becomes slower, we should add more of the replacements.
@pytest.mark.parametrize(
    "num_subjects",
    [
        (1),  # Every iteration should match
        (2),  # Low number of variations, hit rate of about 50%
        (50),  # High number of variations, hit rate of about 2%
        (100),  # High number of variations, hit rate of about 1%
    ],
)
def test_re_matching_for_comparison(benchmark, num_subjects):
    # Generate random strings for the counts we requested
    subjects = [rands() for _ in range(num_subjects)]

    # Create a single pattern to use for all matches
    # Pick a random pattern
    pattern = re.compile(random.choice(subjects))
    cached_match = cachedmethod(pattern.match)

    def func():
        for subject in subjects:
            cached_match(subject)

    benchmark(func)
