from __future__ import annotations

import ast
from dataclasses import dataclass
import fnmatch
from functools import cache
import hashlib
from pathlib import Path
import re
from typing import Any

from ruamel.yaml import YAML  # noqa


REPO = Path(__file__).parents[1]
TESTS = REPO / "tests"
BENCHMARKS = REPO / "benchmarks"
DDTRACE = REPO / "ddtrace"
SEARCH_ROOTS = ((TESTS, ""), (BENCHMARKS, "benchmarks"))
LOCK_ROOT = Path("tests/requirements_locks")
LOCK_PLATFORM = "linux"

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)(\[[A-Za-z0-9_., -]+\])?")

DEFAULT_DEPENDENCIES = (
    "mock",
    "pytest",
    "pytest-mock",
    "coverage",
    "pytest-cov",
    "opentracing",
    "hypothesis<6.45.1",
)
DEFAULT_PYTHON_VERSIONS = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
DEFAULT_ENVIRONMENT = {
    "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER": "1",
    "DD_TESTING_RAISE": "1",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_INJECTION_ENABLED": "1",
    "DD_INJECT_FORCE": "1",
    "DD_PATCH_MODULES": "unittest:false",
    "CMAKE_BUILD_PARALLEL_LEVEL": "12",
    "CARGO_BUILD_JOBS": "12",
    "DD_TRACE_COMPUTE_STATS": "false",
    "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "false",
    "DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS": "2000",
    "_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED": "1",
}
NIGHTLY_ENVIRONMENT = {"DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1"}


class MatrixError(ValueError):
    """Raised when a test matrix declaration is invalid."""


def _inline_local_components(paths: list[str], components: dict[str, list[str]]) -> list[str]:
    """Replace references to components declared in the same suitespec with their patterns.

    >>> _inline_local_components(["@a", "@b", "x"], {"a": ["@c", "y"], "c": ["z"]})
    ['z', 'y', '@b', 'x']
    """
    inlined = []
    for path in paths:
        if path.startswith("@") and path[1:] in components:
            inlined.extend(_inline_local_components(components[path[1:]], components))
        else:
            inlined.append(path)
    return inlined


def _collect_suitespecs() -> dict:
    suitespec: dict[str, dict] = {"components": {}, "suites": {}}

    specfiles = []
    for root, ns_prefix in SEARCH_ROOTS:
        for f in root.rglob("suitespec.yml"):
            specfiles.append((f, root, ns_prefix))

    for s, root, ns_prefix in specfiles:
        path_parts = s.relative_to(root).parts[:-1]
        namespace = "::".join(path_parts) if path_parts else ns_prefix or None
        with YAML(typ="safe") as yaml:
            data = yaml.load(s)
        components = data.get("components", {})
        suitespec["components"].update(components)

        source = s.relative_to(TESTS.parent).as_posix()
        for name, value in data["suites"].items():
            spec = value.copy()
            spec["paths"] = [*_inline_local_components(spec["paths"], components), source]
            full_name = f"{namespace}::{name}" if namespace is not None else name
            if namespace is not None and "pattern" not in spec:
                spec["pattern"] = name
            suitespec["suites"][full_name] = spec

    return suitespec


SUITESPEC = _collect_suitespecs()

# Files that can back an importable ddtrace module. Stubs are leaves of the
# import graph because their imports never run.
_MODULE_SUFFIXES = (".py", ".pyi", ".pyx", ".pxd")
_PARSED_SUFFIXES = (".py", ".pyx", ".pxd")

# Cython is not Python syntax, so its import statements are matched line by line.
# Continuation lines of parenthesized imports are missed, so only their first
# names count.
_CYTHON_IMPORT = re.compile(r"^\s*(?:from\s+(\.*)([\w.]*)\s+c?import\s+(.+)|c?import\s+(.+))$", re.MULTILINE)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


class _ImportCollector(ast.NodeVisitor):
    """Collect the absolute names a module imports.

    Function-level imports count because they run whenever the function does.
    Imports under TYPE_CHECKING are skipped because they never run.
    """

    def __init__(self, module: tuple[str, ...], is_package: bool) -> None:
        self.package = module if is_package else module[:-1]
        self.names: set[tuple[str, ...]] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(tuple(alias.name.split(".")) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = tuple(node.module.split(".")) if node.module else ()
        if node.level:
            module = self.package[: len(self.package) - node.level + 1] + module
        self.names.add(module)
        # The imported names may be submodules; _module_files tells them apart from attributes.
        self.names.update(module + (alias.name,) for alias in node.names if alias.name != "*")


def _cython_imports(source: str, package: tuple[str, ...]) -> set[tuple[str, ...]]:
    r"""Absolute names imported or cimported by Cython source.

    >>> sorted(_cython_imports("from .a cimport b\nimport x.y, z as w\nfrom p import (q,\n    r)\n", ("pkg",)))
    [('p',), ('p', 'q'), ('pkg', 'a'), ('pkg', 'a', 'b'), ('x', 'y'), ('z',)]
    """
    names: set[tuple[str, ...]] = set()
    for dots, module_name, imported, plain in _CYTHON_IMPORT.findall(source):
        aliases = [a.strip(" ()\\").split(" as ")[0].strip() for a in (imported or plain).split("#")[0].split(",")]
        aliases = [a for a in aliases if a]
        if plain:
            names.update(tuple(a.split(".")) for a in aliases)
            continue
        module = tuple(module_name.split(".")) if module_name else ()
        if dots:
            module = package[: len(package) - len(dots) + 1] + module
        names.add(module)
        names.update(module + (a,) for a in aliases if a != "*")
    return names


@cache
def _module_files(module: tuple[str, ...]) -> tuple[str, ...]:
    """Repo-relative files backing a module, or nothing if the name is not a module."""
    base = REPO.joinpath(*module)
    candidates = [base / "__init__.py", *(base.with_suffix(suffix) for suffix in _MODULE_SUFFIXES)]
    return tuple(c.relative_to(REPO).as_posix() for c in candidates if c.is_file())


@cache
def _lazy_exports(package: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Names a package resolves lazily in its module-level __getattr__, mapped to the modules that define them.

    The idiom is a dict of name -> module name, which __getattr__ imports on
    first access, so `from package import Name` really imports that module.

    >>> _lazy_exports(("ddtrace", "llmobs", "_integrations"))["BedrockIntegration"]
    ('ddtrace', 'llmobs', '_integrations', 'bedrock')
    """
    init = REPO.joinpath(*package) / "__init__.py"
    if not init.is_file():
        return {}
    tree = ast.parse(init.read_bytes())
    if not any(isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in tree.body):
        return {}
    exports = {}
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(value, ast.Dict):
            continue
        for key, target in zip(value.keys, value.values):
            if not (isinstance(key, ast.Constant) and isinstance(target, ast.Constant)):
                continue
            if not (isinstance(key.value, str) and isinstance(target.value, str)):
                continue
            relative = target.value.lstrip(".")
            level = len(target.value) - len(relative)
            base = package[: len(package) - level + 1] if level else ()
            resolved = base + tuple(relative.split("."))
            if _module_files(resolved):
                exports[key.value] = resolved
    return exports


@cache
def _direct_imports(path: str) -> frozenset[str]:
    """Files of the ddtrace modules that a ddtrace source file imports itself.

    Only the imported modules count, not their parent packages, so that a
    component depends on what its code names rather than on everything that
    package initialization drags in.

    >>> "ddtrace/internal/datadog/profiling/code_provenance.py" in _direct_imports(
    ...     "ddtrace/internal/datadog/profiling/ddup/_ddup.pyx"
    ... )
    True
    >>> "ddtrace/llmobs/_integrations/bedrock.py" in _direct_imports("ddtrace/contrib/internal/botocore/patch.py")
    True
    """
    parts = Path(path).with_suffix("").parts
    is_package = parts[-1] == "__init__"
    module = parts[:-1] if is_package else parts
    source = (REPO / path).read_bytes()
    if path.endswith(".py"):
        collector = _ImportCollector(module, is_package)
        try:
            collector.visit(ast.parse(source, filename=path))
        except SyntaxError:
            # Dependencies are unknown, so be conservative: any ddtrace change is relevant.
            return frozenset({"ddtrace/*"})
        names = collector.names
    else:
        names = _cython_imports(source.decode(), module[:-1])

    imports = set()
    for name in names:
        if name[:1] != ("ddtrace",):
            continue
        files = _module_files(name)
        if not files:
            lazy = _lazy_exports(name[:-1]).get(name[-1])
            files = _module_files(lazy) if lazy is not None else ()
        imports.update(files)
    imports.discard(path)
    return frozenset(imports)


@cache
def _ddtrace_sources() -> tuple[str, ...]:
    return tuple(sorted(p.relative_to(REPO).as_posix() for p in DDTRACE.rglob("*") if p.suffix in _PARSED_SUFFIXES))


def _literal_prefix(pattern: str) -> str:
    return re.split(r"[*?\[]", pattern, maxsplit=1)[0]


@cache
def _component_matchers() -> tuple[tuple[str, int, re.Pattern[str]], ...]:
    """(component, specificity, regex) for every pattern of every ordinary component."""
    matchers = []
    for component, patterns in SUITESPEC["components"].items():
        if component.startswith("$"):
            continue
        for pattern in patterns:
            if pattern.startswith(("!", "@")):
                continue
            prefix = _literal_prefix(pattern)
            # An exact path is more specific than any glob.
            specificity = len(prefix) + (1 << 16 if prefix == pattern else 0)
            matchers.append((component, specificity, re.compile(fnmatch.translate(pattern))))
    return tuple(matchers)


@cache
def _owners(path: str) -> frozenset[str]:
    """The components owning a file: those whose most specific pattern matches it."""
    hits = [(specificity, component) for component, specificity, rx in _component_matchers() if rx.match(path)]
    if not hits:
        return frozenset()
    best = max(specificity for specificity, _ in hits)
    return frozenset(component for specificity, component in hits if specificity == best)


@cache
def _imported_components(patterns: frozenset[str]) -> frozenset[str]:
    """Components owning the files that the ddtrace sources matching the patterns import directly.

    >>> deps = _imported_components(frozenset({"ddtrace/debugging/*"}))
    >>> {"core", "remoteconfig", "tracing"} <= deps
    True
    >>> "bootstrap" in deps
    False
    """
    matcher = re.compile("|".join(fnmatch.translate(p) for p in patterns))
    components: set[str] = set()
    for source in _ddtrace_sources():
        if matcher.match(source):
            for imported in _direct_imports(source):
                components |= _owners(imported)
    return frozenset(components)


@cache
def get_patterns(suite: str) -> set[str]:
    """Get the patterns for a suite

    The explicit patterns that point into ddtrace (including those of components
    declared in the suite's own suitespec, which are inlined on load) also pull
    in the components that the matching sources import directly. References to
    components declared elsewhere are dependencies and are taken as they are.

    >>> "tests/ci_visibility/suitespec.yml" in get_patterns("ci_visibility::pytest")
    True
    >>> "ddtrace/internal/remoteconfig/*" in get_patterns("debugging::debugger")  # discovered from @debugging
    True
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
    sources = frozenset(p for p in suite_patterns if p.startswith("ddtrace/"))
    if sources:
        suite_patterns |= {f"@{c}" for c in _imported_components(sources)}

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


def get_suites() -> dict[str, dict]:
    """Get the list of suites."""
    return SUITESPEC["suites"]


@dataclass(frozen=True)
class TestRun:
    """One command and environment executed in a test environment."""

    command: str
    env: tuple[tuple[str, str], ...] = ()

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.env)


@dataclass(frozen=True)
class TestEnvironment:
    """A concrete test dependency environment."""

    suite: str
    name: str
    integration_name: str
    python: str
    direct_dependencies: tuple[str, ...]
    runs: tuple[TestRun, ...]

    @property
    def lockfile(self) -> Path:
        return LOCK_ROOT / f"{self.hash}.txt"

    @property
    def hash(self) -> str:
        return _test_environment_hash(self.name, self.python, self.direct_dependencies)


def _requirement_key(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise MatrixError(f"invalid dependency requirement: {requirement}")
    name, extras = match.groups()
    return f"{name}{extras or ''}".lower().replace("_", "-")


def _test_environment_hash(name: str, python: str, dependencies: tuple[str, ...]) -> str:
    packages = " ".join(f"'{dependency}'" for dependency in dependencies)
    payload = f"{name!r}Interpreter(_hint={python!r}){packages}".encode()
    digest = int(hashlib.sha256(payload).hexdigest(), 16)
    return f"{digest % ((1 << 61) - 1):x}"[:7]


def _merge_dependencies(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: dict[str, str] = {}
    for group in groups:
        for requirement in group:
            name = _requirement_key(requirement)
            _, separator, marker = requirement.partition(";")
            key = f"{name};{marker.strip()}" if separator else name
            merged[key] = requirement
    return tuple(merged.values())


def _runs(
    command: str | None, base_environment: dict[str, str], run_specs: list[dict[str, Any]] | None
) -> tuple[TestRun, ...]:
    if run_specs is None:
        if not isinstance(command, str):
            raise MatrixError("each matrix environment needs a command or runs")
        return (TestRun(command=command, env=tuple(sorted(base_environment.items()))),)
    if not run_specs:
        raise MatrixError("runs must not be empty")

    runs = []
    for run in run_specs:
        run_environment = base_environment.copy()
        if "env" in run:
            run_environment.update(run["env"])
        run_command = run.get("command", command)
        if not isinstance(run_command, str):
            raise MatrixError("each matrix run needs a command")
        runs.append(TestRun(command=run_command, env=tuple(sorted(run_environment.items()))))
    return tuple(runs)


def _variant_settings(
    suite: str,
    suite_config: dict[str, Any],
    matrix: dict[str, Any],
    variant: dict[str, Any],
    nightly: bool,
) -> tuple[tuple[str, ...], str, tuple[TestRun, ...]]:
    dependencies = _merge_dependencies(DEFAULT_DEPENDENCIES, tuple(variant.get("dependencies", ())))
    environment = DEFAULT_ENVIRONMENT.copy()
    if nightly:
        environment.update(NIGHTLY_ENVIRONMENT)
    if "env" in matrix:
        environment.update(matrix["env"])
    if "env" in variant:
        environment.update(variant["env"])

    command = variant.get("command", matrix.get("command"))
    run_specs = variant.get("runs", matrix.get("runs"))
    integration = variant.get("integration", suite_config.get("integration", variant["name"].split(":", 1)[0]))
    return dependencies, integration, _runs(command, environment, run_specs)


def _expand_suite_matrix(
    suite: str,
    suite_config: dict[str, Any],
    *,
    nightly: bool,
) -> tuple[TestEnvironment, ...]:
    """Expand one compact suite matrix into concrete test environments."""
    matrix = suite_config["matrix"]
    variants = matrix["variants"]
    if not variants:
        raise MatrixError(f"variants for {suite} must not be empty")

    environments = []
    for variant in variants:
        name = variant.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MatrixError(f"every variant for {suite} needs a name")
        python_value = variant.get("python", matrix.get("python", DEFAULT_PYTHON_VERSIONS))
        python_versions = tuple(python_value)
        if not python_versions:
            raise MatrixError(f"variant {name} for {suite} needs a Python version")
        dependencies, integration, runs = _variant_settings(
            suite,
            suite_config,
            matrix,
            variant,
            nightly,
        )
        for python in python_versions:
            environments.append(
                TestEnvironment(
                    suite=suite,
                    name=name,
                    integration_name=integration,
                    python=python,
                    direct_dependencies=dependencies,
                    runs=runs,
                )
            )

    return tuple(environments)


@cache
def get_test_environments(*, nightly: bool) -> dict[str, tuple[TestEnvironment, ...]]:
    """Return every concrete test environment declared by suitespec."""
    environments = {
        suite: _expand_suite_matrix(suite, config, nightly=nightly)
        for suite, config in get_suites().items()
        if "matrix" in config
    }
    hashes: dict[str, TestEnvironment] = {}
    for matrix in environments.values():
        for environment in matrix:
            if environment.hash in hashes:
                other = hashes[environment.hash]
                raise MatrixError(
                    f"environment hash {environment.hash} is shared by {other.suite}/{other.name} "
                    f"and {environment.suite}/{environment.name}"
                )
            hashes[environment.hash] = environment
    return environments
