"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
from secrets import choice


def random_choice():
    # label weak_randomness_choice
    result = choice([1, 10])
    return result
