from functools import cache
from pathlib import Path
import typing as t

from ruamel.yaml import YAML  # noqa


TESTS = Path(__file__).parents[1] / "tests"


def _collect_suitespecs() -> dict:
    # Recursively search for suitespec.yml in TESTS
    suitespec = {"components": {}, "suites": {}}

    for s in TESTS.rglob("suitespec.yml"):
        try:
            namespace = ".".join(s.relative_to(TESTS).parts[:-1]) or None
        except IndexError:
            namespace = None
        with YAML() as yaml:
            data = yaml.load(s)
            suites = data.get("suites", {})
            if namespace is not None:
                for name, spec in list(suites.items()):
                    if "pattern" not in spec:
                        spec["pattern"] = name
                    suites[f"{namespace}::{name}"] = spec
                    del suites[name]
            for k, v in suitespec.items():
                v.update(data.get(k, {}))

    return suitespec


SUITESPEC = _collect_suitespecs()


@cache
def get_patterns(suite: str) -> t.Set[str]:
    """Get the patterns for a suite

    >>> SUITESPEC["components"] = {"$h": ["tests/s.py"], "core": ["core/*"], "debugging": ["ddtrace/d/*"]}
    >>> SUITESPEC["suites"] = {"debugger": {"paths": ["@core", "@debugging", "tests/d/*"]}}
    >>> sorted(get_patterns("debugger"))  # doctest: +NORMALIZE_WHITESPACE
    ['core/*', 'ddtrace/d/*', 'tests/d/*', 'tests/s.py']
    >>> get_patterns("foobar")
    set()
    """
    compos = SUITESPEC["components"]
    if suite not in SUITESPEC["suites"]:
        return set()

    suite_patterns = set(SUITESPEC["suites"][suite]["paths"])

    # Include patterns from include-always components
    for patterns in (patterns for compo, patterns in compos.items() if compo.startswith("$")):
        suite_patterns |= set(patterns)

    def resolve(patterns: set) -> set:
        refs = {_ for _ in patterns if _.startswith("@")}
        resolved_patterns = patterns - refs

        # Recursively resolve references
        for ref in refs:
            try:
                resolved_patterns |= resolve(set(compos[ref[1:]]))
            except KeyError:
                raise ValueError(f"Unknown component reference: {ref}")

        return resolved_patterns

    return {_.format(suite=suite.replace("::", ".")) for _ in resolve(suite_patterns)}


def get_suites() -> t.Dict[str, dict]:
    """Get the list of suites."""
    return SUITESPEC["suites"]


def get_components() -> t.Dict[str, t.List[str]]:
    """Get the list of jobs."""
    return SUITESPEC.get("components", {})
