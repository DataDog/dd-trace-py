"""boundaries gate: static import, dependency and build-metadata checks.

Pass criteria (docs/package-split/plan.md):

- no import cycles among the manifest's source modules;
- no import edges that the manifest's ``allowed_edges`` policy does not
  declare (longest matching ``from_prefix`` wins), including edges reached
  through parent-package initializers;
- no imports of the manifest's ``forbidden_namespaces`` (static, dynamic or
  parent-initializer);
- every dynamic import either resolves to a literal checked like a static
  edge, or matches an explicit allowlist entry (non-literal dynamic imports
  are rejected by default);
- for ``native-free`` packages: no native build requirements or extension
  modules in build metadata, and no runtime dependency outside the declared
  set (a hidden native dependency cannot sneak in as an undeclared edge).
"""

from __future__ import annotations

import fnmatch
from typing import Any
from typing import Optional

from .._toml import tomllib
from ..astdeps import ImportEdge
from ..astdeps import build_graph
from ..astdeps import find_cycles
from ..evidence import FAIL
from ..evidence import PASS
from ..evidence import SKIP
from ..manifest import Manifest


# Top-level names in build requirements that produce native artifacts.
NATIVE_BUILD_REQUIRES = {
    "cython",
    "setuptools-rust",
    "cmake",
    "ninja",
    "maturin",
    "numpy",
    "patchelf",
}


def _edge_policy(manifest: Manifest) -> list[dict[str, Any]]:
    return sorted(manifest.allowed_edges, key=lambda e: -len(e.get("from_prefix", "")))


def _check_edge(edge: ImportEdge, manifest: Manifest, policy: list[dict[str, Any]]) -> Optional[str]:
    """Return a violation string when the edge is not allowed by policy."""
    imported = edge.imported
    top = imported.split(".")[0]
    if top in manifest.forbidden_namespaces:
        return "forbidden namespace %r" % top

    if top not in manifest.import_roots:
        # External import: allowed only if some matching rule lists it. The
        # transitional single-distribution manifest uses the explicit "*"
        # wildcard; extracted packages must enumerate their externals.
        for rule in policy:
            if _prefix_matches(edge.importer, rule.get("from_prefix", "")):
                if "*" in rule.get("external", []) or top in rule.get("external", []):
                    return None
        return "undeclared external dependency %r" % top

    # Internal edge: the most specific matching rule must allow the target.
    for rule in policy:
        if _prefix_matches(edge.importer, rule.get("from_prefix", "")):
            for to_prefix in rule.get("to_prefixes", []):
                if imported == to_prefix or imported.startswith(to_prefix + "."):
                    return None
            return "undeclared edge %s -> %s" % (edge.importer, imported)
    # No rule matches this importer: undeclared by default (fail closed).
    return "no allowed_edges rule covers importer %r" % edge.importer


def _prefix_matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    graph = build_graph(manifest.root, manifest.source_paths, manifest.import_roots)
    policy = _edge_policy(manifest)

    # 1. Import cycles.
    cycles = find_cycles(graph)
    if cycles:
        report.record(
            "import-cycles",
            FAIL,
            "%d strongly connected components with more than one module" % len(cycles),
            cycles=[[" <-> ".join(c) for c in cycle] for cycle in cycles],
        )
    else:
        report.record("import-cycles", PASS, "no import cycles among %d modules" % len(graph.modules))

    # 2. Edge policy (static, from-import and parent-initializer edges).
    violations = []
    for edge in graph.edges:
        if edge.importer.split(".")[0] not in manifest.import_roots:
            continue
        reason = _check_edge(edge, manifest, policy)
        if reason is not None:
            violations.append(
                {
                    "importer": edge.importer,
                    "imported": edge.imported,
                    "reason": reason,
                    "location": edge.location,
                    "kind": edge.kind,
                    "guarded": edge.guarded,
                    "via_parent_init": edge.via_parent_init,
                }
            )
    if violations:
        report.record(
            "edge-policy",
            FAIL,
            "%d undeclared or forbidden import edges" % len(violations),
            violations=violations[:200],
            total=len(violations),
        )
    elif not policy:
        report.record(
            "edge-policy",
            SKIP,
            "no allowed_edges policy declared; internal edges unchecked",
        )
    else:
        report.record("edge-policy", PASS, "all %d edges allowed" % len(graph.edges))

    # 3. Dynamic imports.
    dynamic_problems = []
    allowlist = list(getattr(args, "dynamic_import_allowlist", None) or [])
    for dynamic in graph.dynamic_imports:
        if dynamic.resolved is None:
            if not any(fnmatch.fnmatch(dynamic.location, pattern) for pattern in allowlist):
                dynamic_problems.append(
                    {"location": dynamic.location, "importer": dynamic.importer, "reason": "non-literal dynamic import"}
                )
        else:
            edge = ImportEdge(
                importer=dynamic.importer,
                imported=dynamic.resolved,
                kind="dynamic",
                guarded=False,
                location=dynamic.location,
            )
            reason = _check_edge(edge, manifest, policy)
            if reason is not None:
                dynamic_problems.append({"location": dynamic.location, "importer": dynamic.importer, "reason": reason})
    if dynamic_problems:
        report.record(
            "dynamic-imports",
            FAIL,
            "%d dynamic imports violate policy or are unresolvable" % len(dynamic_problems),
            violations=dynamic_problems[:200],
        )
    else:
        report.record(
            "dynamic-imports",
            PASS,
            "%d dynamic imports checked" % len(graph.dynamic_imports),
        )

    # 4. Build and runtime metadata vs the declared contract.
    _metadata_checks(manifest, report)


def _pyproject_data(manifest: Manifest) -> Optional[dict[str, Any]]:
    pyproject = manifest.root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        loaded: dict[str, Any] = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return loaded
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _metadata_checks(manifest: Manifest, report: Any) -> None:
    pyproject = _pyproject_data(manifest)
    if pyproject is None:
        report.record(
            "build-metadata",
            FAIL,
            "no readable pyproject.toml next to the manifest",
        )
        return

    problems: list[str] = []

    # 4a. Dependencies shipped in the wheel's metadata must be declared in
    # the manifest (runtime or a named extras group); an undeclared
    # dependency is how native requirements hide.
    project = pyproject.get("project", {})
    declared = set(manifest.runtime_dependencies)
    for group, requirements in manifest.extras.items():
        declared.update(_dep_name(r) for r in requirements)
    extras = project.get("optional-dependencies", {})
    for group, requirements in extras.items():
        for requirement in requirements:
            name = _dep_name(requirement)
            if name not in declared:
                problems.append("extra dependency %r (group %r) is not declared in [dependencies]" % (name, group))
    for requirement in project.get("dependencies", []):
        name = _dep_name(requirement)
        if name not in declared:
            problems.append("runtime dependency %r is not declared in [dependencies]" % name)

    # 4b. Native policy vs build metadata.
    native = manifest.native_policy
    build_system = pyproject.get("build-system", {}).get("requires", [])
    for requirement in build_system:
        name = _dep_name(requirement)
        if name in NATIVE_BUILD_REQUIRES:
            if native == "native-free":
                problems.append("native-free package requires %r at build time" % name)
    if native == "native-free":
        setuptools = pyproject.get("tool", {}).get("setuptools", {})
        if setuptools.get("ext-modules") or pyproject.get("tool", {}).get("cargo"):
            problems.append("native-free package declares extension/cargo modules in build metadata")

    if problems:
        report.record("build-metadata", FAIL, "; ".join(problems), problems=problems)
    else:
        report.record(
            "build-metadata",
            PASS,
            "runtime dependencies and build requirements match the manifest (native policy: %s)" % native,
        )


def _dep_name(requirement: str) -> str:
    """Extract the distribution name from a PEP 508 requirement string.

    Handles version specifiers, extras and environment markers, e.g.
    ``bytecode>=0.16.0,<1; python_version~='3.12.0'`` -> ``bytecode``.
    """
    import re

    name = re.split(r"[<>=!~;\[(\s]", requirement.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")
