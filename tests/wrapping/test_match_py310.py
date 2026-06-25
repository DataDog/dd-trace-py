"""Structural pattern matching (match/case) in a wrapped body — Python 3.10+.

Collected only on 3.10+ (see conftest.py); the match statement compiles to opcodes
the wrapping trampolines must preserve.
"""

from tests.wrapping._harness import mechanisms


@mechanisms
def test_match_in_function_body(mech):
    def f(x):
        match x:
            case (a, b):
                return ("pair", a, b)
            case [*items]:
                return ("seq", len(items))
            case _:
                return ("other", x)

    g = mech.wrap_function(f)
    assert g((1, 2)) == ("pair", 1, 2)
    assert g([1, 2, 3]) == ("seq", 3)
    assert g(99) == ("other", 99)


@mechanisms
def test_match_in_generator_body(mech):
    def g(x):
        match x:
            case (a, b):
                yield ("pair", a, b)
            case _:
                yield ("other", x)

    assert list(mech.wrap_function(g)((1, 2))) == [("pair", 1, 2)]
