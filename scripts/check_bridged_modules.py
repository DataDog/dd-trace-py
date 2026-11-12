#!/usr/bin/env python3
"""Check that modules bridged to extracted packages stay complete.

A bridge module is a ddtrace module whose entire implementation is imports
from an extracted ``_ddtrace_internal*`` distribution (see the
``bridge-extracted-module`` skill). The bridge pattern requires several
coordinated pieces that are easy to forget; this check verifies each one so a
forgotten piece fails loudly instead of surfacing as a silent type or runtime
regression:

1. the extracted distribution is declared in ``[project.dependencies]`` and
   pinned in the ``lint`` dependency group -- mypy runs against the lint group
   only, and a missing pin silently degrades every bridged name to ``Any``;
2. the dependency rows exist in ``requirements.csv`` and
   ``lib-injection/sources/requirements.csv``;
3. a test exercises the import chain (imports both the bridge module and the
   extracted module, asserting the bridge resolves to the extracted objects);
4. every name the bridge re-exports exists on the extracted module;
5. every name other in-repo modules import from the bridge exists on the
   extracted module.

Run via ``scripts/lint bridged-modules`` (part of ``scripts/lint checks``).
"""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Iterator
from typing import Optional


EXTRACTED_PREFIX = "_ddtrace_internal"

# Directories that never contain bridges or their consumers.
_SKIP_DIRS = ("__pycache__", "vendor", "node_modules")

_DEP_LINE = re.compile(r'"([^"]*)"')


@dataclass(frozen=True)
class Bridge:
    """A ddtrace module that only re-imports extracted ``_ddtrace_internal*`` modules."""

    module: str  # dotted module name, e.g. ddtrace.internal.codeowners
    path: Path
    sources: tuple[str, ...]  # extracted dotted modules, e.g. _ddtrace_internal_base.codeowners
    reexports: tuple[str, ...]  # names explicitly re-exported by the bridge

    def distributions(self) -> tuple[str, ...]:
        """Extracted distribution names (``_ddtrace_internal_base`` -> ``ddtrace-internal-base``)."""
        roots = []
        for source in self.sources:
            root = source.split(".")[0]
            if root not in roots:
                roots.append(root)
        return tuple(root.lstrip("_").replace("_", "-") for root in roots)


def _distribution_name(import_root: str) -> str:
    """Map an extracted import root to its distribution name."""
    return import_root.lstrip("_").replace("_", "-")


def _read_text(path: Path) -> Optional[str]:
    """File text, or None when the file is not decodable (e.g. test fixtures)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _statements(tree: ast.Module) -> Iterator[ast.stmt]:
    """Module body statements, with the module docstring filtered out."""
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        yield node


def _bridge_from(tree: ast.Module) -> Optional[Bridge]:
    """Return bridge info when the module only imports extracted packages."""
    imports: list[ast.stmt] = []
    others: list[ast.stmt] = []
    for node in _statements(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        else:
            others.append(node)
    if others or not imports:
        return None

    sources: list[str] = []
    reexports: list[str] = []
    bridged = False
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(EXTRACTED_PREFIX):
            bridged = True
            if node.module not in sources:
                sources.append(node.module)
            reexports.extend(alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(EXTRACTED_PREFIX):
                    bridged = True
                    if alias.name not in sources:
                        sources.append(alias.name)
    if not bridged:
        return None
    return Bridge(module="", path=Path(), sources=tuple(sources), reexports=tuple(reexports))


def _dotted(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def _iter_python(root: Path, *bases: str) -> Iterator[Path]:
    for base in bases:
        for path in sorted((root / base).rglob("*.py")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            yield path


def discover_bridges(root: Path) -> list[Bridge]:
    """All bridge modules under ``root/ddtrace``."""
    bridges = []
    for path in _iter_python(root, "ddtrace"):
        text = _read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        bridge = _bridge_from(tree)
        if bridge is None:
            continue
        bridges.append(
            Bridge(module=_dotted(root, path), path=path, sources=bridge.sources, reexports=bridge.reexports)
        )
    return bridges


def _parse_list_section(text: str, section: str, header: str) -> dict[str, str]:
    """Dependency ``name -> specifier`` mapping from a TOML array of strings.

    Text-based on purpose: the check runs on any interpreter with no
    third-party dependencies, and a full TOML round-trip is not needed to read
    flat ``"name>=spec; marker"`` entries.
    """
    dependencies = {}
    current = None
    in_list = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            in_list = False
            continue
        if current == section and stripped.startswith(header):
            in_list = True
            stripped = stripped[len(header) :].strip()
        if not in_list:
            continue
        if stripped.startswith("]"):
            in_list = False
            continue
        if not stripped.startswith('"'):
            continue  # comment or continuation noise
        match = _DEP_LINE.search(stripped)
        if match is None:
            continue
        entry = match.group(1)
        entry = entry.split(";", 1)[0].strip()
        name_spec = re.match(r"([A-Za-z0-9._-]+)\s*(.*)", entry)
        if name_spec:
            name, spec = name_spec.group(1), name_spec.group(2).strip()
            dependencies[name.strip()] = spec
    return dependencies


def parse_pyproject(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Runtime dependencies and the lint-group pins, as name -> specifier."""
    runtime = _parse_list_section(text, "project", "dependencies = [")
    lint = _parse_list_section(text, "dependency-groups", "lint = [")
    return runtime, lint


def parse_requirements_csv(path: Path) -> dict[str, str]:
    """Dependency rows from a requirements.csv, as name -> specifier."""
    dependencies = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("Dependency") or "").strip()
            spec = (row.get("Version Specifier") or "").strip()
            if name:
                dependencies[name] = spec
    return dependencies


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except ValueError:
        raise ValueError("cannot compare non-numeric version: %s" % version)


def _pin_within_bounds(pin: str, runtime_spec: str) -> Optional[str]:
    """Error message when the lint pin does not satisfy the runtime specifier, else None."""
    minimum = exact = maximum = None
    for part in runtime_spec.split(","):
        part = part.strip()
        if part.startswith(">="):
            minimum = part[2:].strip()
        elif part.startswith("=="):
            exact = part[2:].strip()
        elif part.startswith("<"):
            maximum = part[1:].strip()
        elif part:
            return "unsupported specifier in %s (only >=, == and < are compared)" % runtime_spec
    pinned = _version_tuple(pin)
    if minimum is not None and pinned < _version_tuple(minimum):
        return "lint pin %s is below the runtime minimum %s" % (pin, minimum)
    if maximum is not None and pinned >= _version_tuple(maximum):
        return "lint pin %s is not below the runtime maximum %s" % (pin, maximum)
    if exact is not None and pinned != _version_tuple(exact):
        return "lint pin %s does not match the runtime requirement %s" % (pin, exact)
    return None


class _SourceCache:
    """Imports extracted modules once per check run."""

    def __init__(self) -> None:
        self.modules: dict[str, ModuleType] = {}

    def get(self, source: str) -> ModuleType:
        if source not in self.modules:
            self.modules[source] = importlib.import_module(source)
        return self.modules[source]


def _consumed_names(root: Path, bridge: Bridge) -> dict[str, str]:
    """Names imported from the bridge module elsewhere in the repo, name -> first location."""
    consumed = {}
    for path in _iter_python(root, "ddtrace", "tests", "scripts"):
        if path == bridge.path:
            continue
        text = _read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != bridge.module:
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in consumed:
                    consumed[alias.name] = "%s:%d" % (path.relative_to(root), node.lineno)
    return consumed


def _bridge_test_exists(root: Path, bridge: Bridge) -> bool:
    """Whether a test imports both the bridge module and the extracted module."""
    for path in _iter_python(root, "tests"):
        text = _read_text(path)
        if text is None:
            continue
        if bridge.module in text and any(source in text for source in bridge.sources):
            return True
    return False


def check_bridge(
    bridge: Bridge,
    runtime: dict[str, str],
    lint: dict[str, str],
    requirements: dict[str, str],
    lib_injection_requirements: dict[str, str],
    sources: _SourceCache,
    root: Path,
) -> list[str]:
    """All violations for one bridge module."""
    errors = []
    module = bridge.module

    for distribution in bridge.distributions():
        key = distribution.casefold()
        runtime_spec = next((spec for name, spec in runtime.items() if name.casefold() == key), None)
        if runtime_spec is None:
            errors.append("%s: %s missing from [project.dependencies]" % (module, distribution))
            continue
        lint_spec = next((spec for name, spec in lint.items() if name.casefold() == key), None)
        if lint_spec is None:
            errors.append("%s: %s missing from the lint dependency group" % (module, distribution))
        elif not lint_spec.startswith("=="):
            errors.append(
                "%s: the lint group must pin %s exactly (got %s, expected ==<version>)"
                % (module, distribution, lint_spec)
            )
        else:
            violation = _pin_within_bounds(lint_spec[2:].strip(), runtime_spec)
            if violation is not None:
                errors.append("%s: %s" % (module, violation))

        for label, table in (
            ("requirements.csv", requirements),
            ("lib-injection/sources/requirements.csv", lib_injection_requirements),
        ):
            csv_spec = next((spec for name, spec in table.items() if name.casefold() == key), None)
            if csv_spec is None:
                errors.append("%s: %s missing from %s" % (module, distribution, label))
            elif csv_spec != runtime_spec:
                errors.append(
                    "%s: %s out of date in %s (%s != %s); run scripts/requirements_to_csv.py"
                    % (module, distribution, label, csv_spec, runtime_spec)
                )

    if not _bridge_test_exists(root, bridge):
        errors.append(
            "%s: no test imports both the bridge and %s; add a bridge resolution test"
            % (module, ", ".join(bridge.sources))
        )

    for source in bridge.sources:
        try:
            extracted = sources.get(source)
        except ImportError as exc:
            errors.append(
                "%s: cannot import %s (%s); is the distribution pinned in the lint group?" % (module, source, exc)
            )
            continue
        for name in bridge.reexports:
            if not hasattr(extracted, name):
                errors.append("%s: re-exported name %s missing from %s" % (module, name, source))

    consumed = _consumed_names(root, bridge)
    for name, location in sorted(consumed.items()):
        if any(hasattr(sources.get(source), name) for source in bridge.sources):
            continue
        errors.append(
            "%s: %s imports %s (%s) but no extracted source provides it"
            % (module, location, name, ", ".join(bridge.sources))
        )

    return errors


def check(root: Path) -> tuple[list[Bridge], list[str]]:
    """Discover bridges under root and return them with all violations found."""
    bridges = discover_bridges(root)
    if not bridges:
        return bridges, []

    errors = []
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    runtime, lint = parse_pyproject(text)
    requirements = parse_requirements_csv(root / "requirements.csv")
    lib_injection_requirements = parse_requirements_csv(root / "lib-injection" / "sources" / "requirements.csv")
    sources = _SourceCache()
    for bridge in bridges:
        errors.extend(check_bridge(bridge, runtime, lint, requirements, lib_injection_requirements, sources, root))
    return bridges, errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to check (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    bridges, errors = check(args.root)
    if not bridges:
        print("No bridged modules found under %s." % args.root)
        return 0
    for bridge in bridges:
        print("bridge: %s -> %s" % (bridge.module, ", ".join(bridge.sources)))
    for error in errors:
        print("error: %s" % error, file=sys.stderr)
    print("Validated %d bridged module(s); %d error(s)." % (len(bridges), len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
