import pytest

from ddtrace.settings.matching import Trie


@pytest.mark.parametrize("key", ["foo", "bar", "baz"])
def test_exact_match(key):
    matcher = Trie()
    matcher.insert("foo")
    matcher.insert("bar")
    matcher.insert("baz")

    m = matcher.match_damreau_levhenstein(key, 0)

    assert m is not None
    assert m.match == key
    assert m.cost == 0


@pytest.mark.parametrize("key", ["fooo", "bad", "ar"])
def test_exact_match_fail(key):
    matcher = Trie()
    matcher.insert("foo")
    matcher.insert("bar")
    matcher.insert("baz")

    assert matcher.match_damreau_levhenstein(key, 0) is None


@pytest.mark.parametrize(
    "key,expected,distance",
    [
        ("fooo", "foo", 1),
        ("bor", "bar", 1),
        ("az", "baz", 1),
        ("fooba", "foo", 2),
        ("ofo", "foo", 1),
        ("brad", "bar", 2),
    ],
)
def test_match_fuzzy(key, expected, distance):
    matcher = Trie()
    matcher.insert("foo")
    matcher.insert("bar")
    matcher.insert("baz")

    m = matcher.match_damreau_levhenstein(key)
    assert m is not None
    assert m.cost == distance
    assert m.match == expected


@pytest.mark.parametrize("key,expected,max_distance", [("fooo", "foo", 2), ("bor", "bar", 1)])
def test_match_fuzzy_under_max_dist(key, expected, max_distance):
    matcher = Trie()
    matcher.insert("foo")
    matcher.insert("bar")
    matcher.insert("baz")

    m = matcher.match_damreau_levhenstein(key, max_distance)
    assert m is not None
    assert m.match == expected


@pytest.mark.parametrize("key,max_distance", [("ooff", 1), ("bor", 0), ("fooba", 1)])
def test_match_fuzzy_over_max_dist(key, max_distance):
    matcher = Trie()
    matcher.insert("foo")
    matcher.insert("bar")
    matcher.insert("baz")

    m = matcher.match_damreau_levhenstein(key, max_distance)
    assert m is None
