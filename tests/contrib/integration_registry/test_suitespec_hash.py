from __future__ import annotations

from tests.suitespec import _test_environment_hash


_NAME: str = "contrib::flask"
_PYTHON: str = "3.12"
# Mixed case so a default sorted() payload differs from str.casefold.
_DEPS: tuple[str, ...] = ("requests", "Zope", "Flask>=2")
_DIGEST: str = "143e39bc2db5"


def test_environment_hash_digest_is_stable() -> None:
    digest: str = _test_environment_hash(_NAME, _PYTHON, _DEPS)

    assert digest == _DIGEST
    assert len(digest) == 12
    assert set(digest) <= set("0123456789abcdef")


def test_environment_hash_is_independent_of_dependency_order() -> None:
    forward: str = _test_environment_hash(_NAME, _PYTHON, _DEPS)
    reversed_deps: tuple[str, ...] = tuple(reversed(_DEPS))
    scrambled: tuple[str, ...] = ("Zope", "Flask>=2", "requests")

    assert _test_environment_hash(_NAME, _PYTHON, reversed_deps) == forward
    assert _test_environment_hash(_NAME, _PYTHON, scrambled) == forward


def test_environment_hash_is_independent_of_casefold_tie_order() -> None:
    # Same casefolded value, different original strings — YAML order must not matter.
    forward: tuple[str, ...] = ("Foo", "foo")
    reverse: tuple[str, ...] = ("foo", "Foo")

    assert _test_environment_hash(_NAME, _PYTHON, forward) == _test_environment_hash(_NAME, _PYTHON, reverse)


def test_environment_hash_changes_when_inputs_change() -> None:
    baseline: str = _test_environment_hash(_NAME, _PYTHON, _DEPS)

    assert _test_environment_hash("other", _PYTHON, _DEPS) != baseline
    assert _test_environment_hash(_NAME, "3.11", _DEPS) != baseline
    assert _test_environment_hash(_NAME, _PYTHON, (*_DEPS, "httpx")) != baseline


def test_test_environment_hash_property_matches_helper() -> None:
    from tests.suitespec import TestEnvironment

    environment: TestEnvironment = TestEnvironment(
        suite="contrib::flask",
        name=_NAME,
        integration_name="flask",
        python=_PYTHON,
        direct_dependencies=_DEPS,
        runs=(),
    )

    assert environment.hash == _DIGEST
