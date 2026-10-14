"""Static import analysis for the boundaries gate.

A self-contained, stdlib-only AST scanner that builds a module graph over the
manifest's declared source paths. It deliberately does not depend on
scripts/import-analysis (betsy) so the harness works from any checkout and in
plain test environments; repo-internal layer policy stays owned by
scripts/import-analysis.

What it records:

- static imports (``import a.b``, ``from a.b import c``) with locations;
- guarded imports (inside ``try/except ImportError``) -- still edges, but
  flagged, because a broad guard can hide a broken provider;
- literal dynamic imports (``importlib.import_module("a.b")``, ``__import__``)
  which are checked like any other edge;
- unresolved dynamic imports (non-literal argument), which the boundaries
  gate rejects unless explicitly allowlisted;
- parent-initializer expansion: importing ``a.b.c`` first imports ``a`` and
  ``a.b``, so those implicit edges are added and flagged, which catches
  forbidden imports hidden in a parent package ``__init__``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


IMPORT_CALLS = ("import_module", "__import__", "importlib.import_module")


@dataclass(frozen=True)
class ImportEdge:
    importer: str
    imported: str
    kind: str  # "static" | "dynamic" | "from-import"
    guarded: bool
    location: str  # file:line
    via_parent_init: bool = False


@dataclass
class DynamicImport:
    importer: str
    location: str
    resolved: Optional[str]  # module name when the argument was a literal


@dataclass
class ModuleGraph:
    import_roots: list[str]
    modules: dict[str, Path]  # module name -> source file
    edges: list[ImportEdge]
    dynamic_imports: list[DynamicImport]
    external: dict[str, set[str]]  # importer module -> external top-level names

    def internal_edges(self) -> list[ImportEdge]:
        return [e for e in self.edges if e.imported.split(".")[0] in self.import_roots]


def _module_names_for(root: Path, source_paths: Iterable[Path], exclude_dirs: tuple[str, ...]) -> dict[str, Path]:
    """Map each .py file under the source paths to its dotted module name.

    Names are derived relative to ``root`` (the manifest directory), which is
    the same convention the repository layout uses: ``ddtrace/internal/x.py``
    is the module ``ddtrace.internal.x``. Vendored third-party code (the
    ``vendor/`` convention, e.g. ddtrace.vendor) is skipped: we do not control
    its structure and it is excluded from cycle and layer policy in
    scripts/import-analysis for the same reason.
    """
    modules: dict[str, Path] = {}
    for source_path in source_paths:
        base = source_path if source_path.is_dir() else source_path.parent
        for py_file in sorted(base.rglob("*.py")):
            rel = py_file.relative_to(root)
            if any(part in exclude_dirs for part in rel.parts):
                continue
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            name = ".".join(parts)
            if name not in modules:
                modules[name] = py_file
    return modules


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, importer: str, file: str, graph: ModuleGraph) -> None:
        self.importer = importer
        self.file = file
        self.graph = graph
        # Stack of try handlers currently being visited, to flag guarded
        # imports (guarded != harmless: the plan requires fallbacks to fail
        # loudly, so guards are recorded, not ignored).
        self._guarded: list[bool] = []

    def _location(self, node: ast.AST) -> str:
        return "%s:%d" % (self.file, getattr(node, "lineno", 0))

    def _record(self, imported: str, kind: str, guarded: bool, location: str) -> None:
        self.graph.edges.append(ImportEdge(self.importer, imported, kind, guarded, location))

    def visit_Try(self, node: ast.Try) -> None:
        guarded = self._is_import_guard(node)
        self._guarded.append(guarded)
        for stmt in node.body:
            self.visit(stmt)
        self._guarded.pop()
        for handler in node.handlers:
            for stmt in handler.body:
                self.visit(stmt)
        for stmt in node.orelse + node.finalbody:
            self.visit(stmt)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        for stmt in node.body:
            self.visit(stmt)

    @staticmethod
    def _is_import_guard(node: ast.Try) -> bool:
        for handler in node.handlers:
            name = handler.type
            if name is None:
                return True
            if isinstance(name, ast.Name) and name.id in ("ImportError", "ModuleNotFoundError"):
                return True
            if isinstance(name, ast.Tuple):
                for elt in name.elts:
                    if isinstance(elt, ast.Name) and elt.id in ("ImportError", "ModuleNotFoundError"):
                        return True
        return False

    def visit_Import(self, node: ast.Import) -> None:
        guarded = bool(self._guarded)
        for alias in node.names:
            self._record(alias.name, "static", guarded, self._location(node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        guarded = bool(self._guarded)
        location = self._location(node)
        if node.level:  # relative import: resolve against the importing package
            # The reference package for relative imports is __package__: for a
            # plain module it is the parent package, but for a package
            # __init__ it is the package itself.
            is_package_init = self.file.replace("\\", "/").endswith("__init__.py")
            importer_parts = self.importer.split(".")
            package_base = importer_parts if is_package_init else importer_parts[:-1]
            # from . (level 1) refers to package_base itself; each extra dot
            # goes one package further up.
            drop = max(node.level - 1, 0)
            package_parts = package_base[: len(package_base) - drop] if drop else package_base
            base_parts = node.module.split(".") if node.module else []
            target = ".".join(package_parts + base_parts)
            if target:
                self._record(target, "from-import", guarded, location)
            for alias in node.names:
                # ``from pkg import name`` may import the submodule pkg.name
                # rather than an attribute of pkg; record both interpretations
                # so the boundaries gate never misses a real module edge.
                candidate = ".".join([target, alias.name]) if target else alias.name
                if candidate in self.graph.modules:
                    self._record(candidate, "from-import", guarded, location)
                elif target:
                    self._record(target, "from-import", guarded, location)
        else:
            module_target: str | None = node.module
            if module_target:
                self._record(module_target, "from-import", guarded, location)
            for alias in node.names:
                if module_target:
                    candidate = "%s.%s" % (module_target, alias.name)
                    if candidate in self.graph.modules:
                        self._record(candidate, "from-import", guarded, location)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in IMPORT_CALLS and node.args:
            arg = node.args[0]
            location = self._location(node)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                resolved: Optional[str] = arg.value
                self.graph.dynamic_imports.append(DynamicImport(self.importer, location, resolved))
            else:
                self.graph.dynamic_imports.append(DynamicImport(self.importer, location, None))
        self.generic_visit(node)


def build_graph(
    root: Path,
    source_paths: Iterable[Path],
    import_roots: list[str],
    exclude_dirs: tuple[str, ...] = ("vendor", "node_modules"),
) -> ModuleGraph:
    modules = _module_names_for(root, source_paths, exclude_dirs)
    graph = ModuleGraph(
        import_roots=list(import_roots),
        modules=modules,
        edges=[],
        dynamic_imports=[],
        external={},
    )
    for module, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        collector = _ImportCollector(module, str(path.relative_to(root)), graph)
        collector.visit(tree)
    graph.external = _collect_external(graph)
    graph.edges = _expand_parent_edges(graph)
    return graph


def _collect_external(graph: ModuleGraph) -> dict[str, set[str]]:
    """Top-level names imported from outside the manifest's import roots."""
    external: dict[str, set[str]] = {}
    for edge in graph.edges:
        top = edge.imported.split(".")[0]
        if top not in graph.import_roots:
            external.setdefault(edge.importer, set()).add(top)
    return external


def _expand_parent_edges(graph: ModuleGraph) -> list[ImportEdge]:
    """Importing a.b.c implies executing a/__init__ and a/b/__init__.

    Returns the full edge list with one flagged implicit edge per missing
    intermediate parent, so a forbidden import hidden in a parent initializer
    is attributed to the importing module as well.
    """
    seen = {(e.importer, e.imported, e.via_parent_init): e for e in graph.edges}
    result: list[ImportEdge] = list(graph.edges)
    for edge in graph.edges:
        parts = edge.imported.split(".")
        if len(parts) < 2:
            continue
        for depth in range(1, len(parts)):
            parent = ".".join(parts[:depth])
            key = (edge.importer, parent, True)
            if key in seen:
                continue
            if parent not in graph.modules:
                continue
            seen[key] = edge
            result.append(
                ImportEdge(
                    importer=edge.importer,
                    imported=parent,
                    kind=edge.kind,
                    guarded=edge.guarded,
                    location=edge.location,
                    via_parent_init=True,
                )
            )
    return result


def find_cycles(graph: ModuleGraph) -> list[list[str]]:
    """Tarjan's strongly connected components (iterative); SCCs > 1 are cycles."""
    internal = set(graph.modules)
    adjacency: dict[str, set[str]] = {m: set() for m in graph.modules}
    for edge in graph.edges:
        if edge.importer in internal and edge.imported in internal:
            adjacency[edge.importer].add(edge.imported)

    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    for start in sorted(adjacency):
        if start in index:
            continue
        work: list[tuple[str, list[str], int]] = [(start, sorted(adjacency[start]), 0)]
        while work:
            node, neighbors, next_i = work[-1]
            if next_i == 0:
                index[node] = index_counter[0]
                lowlink[node] = index_counter[0]
                index_counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            if next_i < len(neighbors):
                work[-1] = (node, neighbors, next_i + 1)
                nxt = neighbors[next_i]
                if nxt not in index:
                    work.append((nxt, sorted(adjacency[nxt]), 0))
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index[nxt])
            else:
                work.pop()
                if lowlink[node] == index[node]:
                    component: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        cycles.append(sorted(component))
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
    return cycles
