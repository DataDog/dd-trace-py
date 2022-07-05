import random
import re
import string

from ddtrace.internal.glob_matching import GlobMatcher


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def test_glob_matching(benchmark):
    # Generate random strings for the counts we requested
    subjects = [rands() for _ in range(25)]

    # Create a single pattern to use for all matches
    # Pick a random pattern
    pattern = random.choice(subjects)
    rule = GlobMatcher(pattern)

    def func():
        for subject in subjects:
            x = rule.match(subject)  # noqa: F841

    benchmark(func)


# This comparison is without all of the character replacing we'd have to do
# in order to  make the regex expression be evaluated as a glob expression.
# Still the glob match is faster, if the glob becomes slower, we should add more of the replacements.
def test_re_matching_for_comparison(benchmark):
    # Generate random strings for the counts we requested
    subjects = [rands() for _ in range(25)]

    # Create a single pattern to use for all matches
    # Pick a random pattern
    pattern = random.choice(subjects)

    def func():
        for subject in subjects:
            # * in Glob is the equivalent of . in re
            subject.replace("*", ".")
            if re.match(pattern, subject):
                x = True  # noqa: F841

    benchmark(func)
