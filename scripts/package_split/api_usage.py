"""Used-API analysis: what other modules actually consume from a target.

Complements the declared contract: ``[contracts].supported_exports`` records
what a package promises, this module computes what the rest of the repository
actually uses -- the de-facto API surface that must survive an extraction
(e.g. when moving ``ddtrace.internal.coverage`` to its own namespace).

Detection is static and over-approximating by design:

- every import binding of the target (``import a.b as c``, ``from a.b import
  x``, relative imports, star imports) is tracked per consumer module;
- usages are resolved through alias tracking and attribute chains rooted at
  module bindings (``c.x.y`` -> ``a.b.x.y``);
- each usage is classified by context: call, subclassed, exception-caught,
  raised, or plain read;
- usages inside the target itself are excluded (self-usage is not an
  external contract), and usages behind ``try/except ImportError`` still
  count as usages (the fallback exists because the symbol is consumed).

Over-approximation is the safe direction for pinning a stable API: chains
that look like ``module.Class.attr`` are reported as deep symbol paths, and a
human decides which paths are really part of the supported surface.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from .astdeps import build_graph


@dataclass(frozen=True)
class Usage:
    importer: str
    symbol: str  # dotted path under the target module, "*" for star imports
    kind: str  # import | call | subclassed | exception-caught | raised | read
    location: str  # file:line


@dataclass
class ApiUsageReport:
    target: str
    usages: list[Usage] = field(default_factory=list)
    files_scanned: int = 0
    consumer_files: int = 0

    def symbols(self) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Aggregate per symbol: kind -> usage records."""
        symbols: dict[str, dict[str, list[dict[str, str]]]] = {}
        for usage in self.usages:
            by_kind = symbols.setdefault(usage.symbol, {})
            by_kind.setdefault(usage.kind, []).append(
                {
                    "importer": usage.importer,
                    "location": usage.location,
                }
            )
        return symbols

    def summary(self) -> str:
        symbols = self.symbols()
        lines = [
            "used-API surface of %s: %d symbol(s) consumed by %d file(s)"
            % (self.target, len(symbols), self.consumer_files)
        ]
        for symbol in sorted(symbols):
            kinds = symbols[symbol]
            kind_summary = ", ".join("%s x%d" % (kind, len(records)) for kind, records in sorted(kinds.items()))
            importers = sorted({r["importer"] for records in kinds.values() for r in records})
            lines.append("  %-60s %s" % (symbol, kind_summary))
            lines.append("      consumers: %s" % ", ".join(importers[:12]))
        return "\n".join(lines)


class _UsageCollector(ast.NodeVisitor):
    """Two passes per consumer module: bind imports, then record usages.

    With a ``target`` prefix, only usages of that module are recorded
    (api-usage mode). With ``target=None`` and ``roots``, every usage whose
    resolved path starts at one of ``roots`` is recorded with its full
    resolved path, so a single pass can later be attributed to owning modules
    (api-surface mode).
    """

    def __init__(
        self,
        importer: str,
        file: str,
        target: str | None,
        report: ApiUsageReport,
        roots: set[str] | None = None,
    ) -> None:
        self.importer = importer
        self.file = file
        self.target = target
        self.report = report
        self.roots = roots
        self.bindings: dict[str, str] = {}
        self.parents: dict[ast.AST, ast.AST] = {}

    # -- pass 1: bindings --------------------------------------------------
    def _is_package_init(self) -> bool:
        return self.file.replace("\\", "/").endswith("__init__.py")

    def _resolve_relative(self, level: int, module: str | None) -> str:
        importer_parts = self.importer.split(".")
        package_base = importer_parts if self._is_package_init() else importer_parts[:-1]
        drop = max(level - 1, 0)
        package_parts = package_base[: len(package_base) - drop] if drop else package_base
        base_parts = module.split(".") if module else []
        return ".".join(package_parts + base_parts)

    def _matches(self, resolved: str) -> bool:
        """Does a resolved path fall within the analysis scope?"""
        if self.target is not None:
            return resolved == self.target or resolved.startswith(self.target + ".")
        top = resolved.split(".")[0]
        if self.roots is None:
            return False
        return top in self.roots

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if alias.asname:
                self.bindings[alias.asname] = alias.name
                if self._matches(alias.name):
                    # `import <module> as x`: the module object is consumed.
                    self.report.usages.append(
                        Usage(
                            self.importer,
                            alias.name if self.target is None else "<module>",
                            "import",
                            self._location(node),
                        )
                    )
            else:
                # `import a.b.c` binds the name `a` to the top-level module.
                self.bindings.setdefault(top, top)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            target_module = self._resolve_relative(node.level, node.module)
        else:
            target_module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                # Star import: everything re-exported is consumed; record the
                # star itself so the reviewer knows the surface is open.
                if self._matches(target_module):
                    star_symbol = "%s.*" % target_module if self.target is None else "*"
                    self.report.usages.append(Usage(self.importer, star_symbol, "import", self._location(node)))
                continue
            binding = "%s.%s" % (target_module, alias.name) if target_module else alias.name
            self.bindings[alias.asname or alias.name] = binding
            if self._matches(binding):
                # The import itself is the baseline usage fact: the symbol is
                # consumed even if no qualified use site is detected.
                if self.target is None:
                    symbol = binding
                else:
                    symbol = "<module>" if binding == self.target else binding[len(self.target) + 1 :]
                self.report.usages.append(Usage(self.importer, symbol, "import", self._location(node)))

    # -- pass 2: usages ----------------------------------------------------
    def _location(self, node: ast.AST) -> str:
        return "%s:%d" % (self.file, getattr(node, "lineno", 0))

    def _record(self, resolved: str, node: ast.AST) -> None:
        if not self._matches(resolved):
            return
        if self.target is not None:
            symbol = "<module>" if resolved == self.target else resolved[len(self.target) + 1 :]
        else:
            symbol = resolved
        self.report.usages.append(Usage(self.importer, symbol, self._kind(node), self._location(node)))

    def _kind(self, node: ast.AST) -> str:
        """Classify a usage by its syntactic context (nearest few parents).

        All candidate kinds in the enclosing window are collected, then the
        strongest wins: the node inside ``raise E('x')`` is both called (to
        construct the instance) and raised, and "raised" is the decisive
        fact for API stability (removing the class breaks the raise site).
        """
        # Priority: how the symbol's *identity* is load-bearing.
        priority = ["exception-caught", "raised", "subclassed", "call", "read"]
        candidates = set()
        current = node
        for _ in range(4):
            parent = self.parents.get(current)
            if parent is None:
                break
            if isinstance(parent, ast.ExceptHandler):
                # Only the handler's *type* expression counts as caught:
                # the body is a normal execution context (a raise inside an
                # except block is "raised", not "caught").
                type_node = parent.type
                if current is type_node or (isinstance(type_node, ast.Tuple) and current in type_node.elts):
                    candidates.add("exception-caught")
            if isinstance(parent, ast.Raise):
                candidates.add("raised")
            if isinstance(parent, ast.Call) and parent.func is current:
                candidates.add("call")
            if isinstance(parent, ast.ClassDef) and current in parent.bases:
                candidates.add("subclassed")
            current = parent
        for kind in priority:
            if kind in candidates:
                return kind
        return "read"

    def _chain_root(self, node: ast.Attribute) -> tuple[str, list[str]] | None:
        """Resolve an attribute chain down to its root Name binding.

        Returns (root_binding, attr_names) when the chain is rooted at a
        tracked binding, else None.
        """
        attrs: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            attrs.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name) and current.id in self.bindings:
            return self.bindings[current.id], attrs
        return None

    def _scan_usages(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                root = self._chain_root(node)
                if root is not None:
                    binding, attrs = root
                    self._record(".".join([binding] + list(reversed(attrs))), node)
            elif isinstance(node, ast.Name):
                name_binding = self.bindings.get(node.id)
                if name_binding is not None:
                    self._record(name_binding, node)


def analyze_api_usage(
    root: Path,
    source_paths: Iterable[Path],
    import_roots: list[str],
    target: str,
) -> ApiUsageReport:
    """Compute the used-API report for ``target`` across the source paths.

    ``import_roots`` only seeds the module graph (module naming); usages are
    matched purely on the resolved target prefix.
    """
    graph = build_graph(root, source_paths, import_roots)
    report = ApiUsageReport(target=target)
    report.files_scanned = len(graph.modules)

    consumers = 0
    for module, path in sorted(graph.modules.items()):
        # The target's own modules consume themselves by definition; only
        # external consumers define the extraction surface.
        if module == target or module.startswith(target + "."):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        collector = _UsageCollector(module, str(path.relative_to(root)), target, report)
        # Pass 1: bindings (imports). Pass 2 must see assignments inside
        # function bodies too, so a plain two-visit order is not enough --
        # bindings are collected first, usages scanned after.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                collector.visit_Import(node)
            elif isinstance(node, ast.ImportFrom):
                collector.visit_ImportFrom(node)
        before = len(report.usages)
        collector._scan_usages(tree)
        if len(report.usages) > before:
            consumers += 1
    report.consumer_files = consumers
    return report
