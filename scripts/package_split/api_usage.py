"""Used-API analysis: what other modules actually consume from a target.

Computes what the rest of the repository actually uses from a module -- the
de-facto API surface that must survive an extraction (e.g. when moving
``ddtrace.internal.coverage`` to its own package).

Detection is static and over-approximating by design:

- every import binding of the target (``import a.b as c``, ``from a.b import
  x``, relative imports, star imports) is tracked per consumer module;
- usages are resolved through alias tracking and attribute chains rooted at
  module bindings (``c.x.y`` -> ``a.b.x.y``), and *through re-export
  chains*: ``from m import x`` where m does ``from a.b import x`` is
  attributed to ``a.b.x`` (this is how public facades re-extract into a
  used-API surface -- a direct-import-only view misses those consumers);
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
import tokenize


_EXCLUDED_DIRS = ("vendor", "node_modules")


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
    # Files this interpreter cannot parse (e.g. newer syntax), skipped and reported.
    unparsed: list[str] = field(default_factory=list)

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

    ``origins`` maps each scanned module's import bindings
    (``{module: {name: {dotted origin}}}``); it is what makes re-export
    chains resolvable. It stays empty in api-surface mode, which remains
    direct-import only.
    """

    def __init__(
        self,
        importer: str,
        file: str,
        target: str | None,
        report: ApiUsageReport,
        roots: set[str] | None = None,
        origins: dict[str, dict[str, set[str]]] | None = None,
    ) -> None:
        self.importer = importer
        self.file = file
        self.target = target
        self.report = report
        self.roots = roots
        self.origins = origins if origins is not None else {}
        # A name can be bound to several modules (try/except ImportError
        # fallbacks, imports in separate function scopes); keep every
        # alternative so usages over-approximate rather than go missing.
        self.bindings: dict[str, set[str]] = {}
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

    def _bind(self, name: str, resolved: str) -> None:
        self.bindings.setdefault(name, set()).add(resolved)

    def _symbol(self, resolved: str) -> str:
        if self.target is None:
            return resolved
        return "<module>" if resolved == self.target else resolved[len(self.target) + 1 :]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self._bind(alias.asname, alias.name)
            else:
                # `import a.b.c` binds the name `a` to the top-level module.
                self._bind(alias.name.split(".")[0], alias.name.split(".")[0])
            if self._matches(alias.name):
                # The imported module is consumed even without later use sites
                # (e.g. side-effect imports).
                self.report.usages.append(
                    Usage(self.importer, self._symbol(alias.name), "import", self._location(node))
                )

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
            self._bind(alias.asname or alias.name, binding)
            if self._matches(binding):
                # The import itself is the baseline usage fact: the symbol is
                # consumed even if no qualified use site is detected.
                self.report.usages.append(Usage(self.importer, self._symbol(binding), "import", self._location(node)))
            else:
                # Re-export chains: `from m import x` where m binds x via
                # `from a.b import x` transitively consumes a.b.x. Follow the
                # chain, attribute the import fact to the origin, and bind
                # the local name to it so later use sites classify against
                # the real owner rather than the facade.
                for origin in self._resolve_chain(target_module, [alias.name]):
                    if origin != binding and self._matches(origin):
                        self._bind(alias.asname or alias.name, origin)
                        self.report.usages.append(
                            Usage(self.importer, self._symbol(origin), "import", self._location(node))
                        )

    # -- pass 2: usages ----------------------------------------------------
    def _location(self, node: ast.AST) -> str:
        return "%s:%d" % (self.file, getattr(node, "lineno", 0))

    def _record(self, resolved: str, node: ast.AST) -> None:
        if not self._matches(resolved):
            return
        self.report.usages.append(Usage(self.importer, self._symbol(resolved), self._kind(node), self._location(node)))

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
        parent = self.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            # A prefix of a longer chain (L in L.f()): the chain's last
            # attribute carries the usage context, the prefix is only read.
            return "read"
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
            # Only the raised expression or its constructor is raised; helpers
            # called to build the exception arguments are ordinary calls.
            if isinstance(parent, ast.Raise) and (
                node is parent.exc or (isinstance(parent.exc, ast.Call) and node is parent.exc.func)
            ):
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

    def _chain_root(self, node: ast.Attribute) -> tuple[set[str], list[str]] | None:
        """Resolve an attribute chain down to its root Name binding.

        Returns (root_bindings, attr_names) when the chain is rooted at a
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

    def _resolve_chain(self, binding: str, attrs: list[str]) -> list[str]:
        """Every dotted path a module-rooted chain can mean, through re-exports.

        ``r.x.y`` with r bound to m means ``m.x.y``; if m binds x via
        ``from a.b import x`` it also means ``a.b.x.y`` -- and x may be
        re-exported through any depth of facades (m -> m2 -> a.b), so every
        new path's re-export bindings are followed transitively. The walk is
        iterative and cycle-safe (each path expands once); the branch factor
        per attribute hop is capped so pathological re-export graphs cannot
        explode.
        """
        paths: list[str] = [binding]
        for attr in attrs:
            # Append the attribute to every known prefix, then follow each
            # new path's re-export bindings transitively.
            frontier = ["%s.%s" % (path, attr) if path else attr for path in paths]
            expanded: list[str] = []
            while frontier:
                current = frontier.pop()
                if current in expanded:
                    continue
                expanded.append(current)
                owner, _, name = current.rpartition(".")
                if owner:
                    for origin in self.origins.get(owner, {}).get(name, ()):
                        if origin not in expanded:
                            frontier.append(origin)
            paths = expanded[:8]
            if not paths:
                break
        return paths

    def _scan_usages(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                root = self._chain_root(node)
                if root is not None:
                    bindings, attrs = root
                    for binding in sorted(bindings):
                        for resolved in self._resolve_chain(binding, list(reversed(attrs))):
                            self._record(resolved, node)
            elif isinstance(node, ast.Name):
                for binding in sorted(self.bindings.get(node.id, ())):
                    self._record(binding, node)


def module_names_for(root: Path, source_paths: Iterable[Path]) -> dict[str, Path]:
    """Map each .py file under the source paths to its dotted module name.

    Names are relative to root: ddtrace/internal/x.py is ddtrace.internal.x.
    Module trees may also live under a ``src`` directory -- at the scan root
    (src/pkg/x.py) or in a workspace layout (packages/<dist>/src/pkg/x.py) --
    so naming starts after the *last* ``src`` path segment. Vendored code
    (vendor/) is skipped because we do not control its structure.
    """
    modules: dict[str, Path] = {}
    for source_path in source_paths:
        if not source_path.exists():
            raise ValueError("source path does not exist: %s" % source_path)
        files = sorted(source_path.rglob("*.py")) if source_path.is_dir() else [source_path]
        for py_file in files:
            rel = py_file.relative_to(root)
            if "src" in rel.parts:
                rel = Path(*rel.parts[len(rel.parts) - rel.parts[::-1].index("src") :])
            if any(part in _EXCLUDED_DIRS for part in rel.parts):
                continue
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                modules.setdefault(".".join(parts), py_file)
    return modules


def parse_source(path: Path) -> ast.Module:
    """Parse a Python file, honoring its PEP 263 encoding declaration."""
    with tokenize.open(path) as f:
        return ast.parse(f.read(), filename=str(path))


def analyze_api_usage(
    root: Path, source_paths: Iterable[Path], target: str, require_target: bool = True
) -> ApiUsageReport:
    """Compute the used-API report for target across the source paths.

    ``require_target=False`` is for consumer-only scopes (e.g. scanning
    ``tests/`` for a module that lives under ``ddtrace/``): the target
    itself is not scanned, so only its consumers are reported. Note that
    re-export attribution needs the re-exporting module inside the scanned
    scope; when in doubt, scan the target's source paths too.
    """
    modules = module_names_for(root, source_paths)
    if require_target and not any(module == target or module.startswith(target + ".") for module in modules):
        # An empty report would be indistinguishable from an unused target
        # (or a typo), unless the caller explicitly scopes to consumers only.
        raise ValueError(
            "no scanned module matches %s; pass the target's source path too, or use --allow-absent-target "
            "for a consumer-only scan" % target
        )
    report = ApiUsageReport(target=target, files_scanned=len(modules))

    # One parse per module, shared by both passes below.
    trees: dict[str, ast.Module | None] = {}
    for module, path in sorted(modules.items()):
        try:
            trees[module] = parse_source(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            trees[module] = None
            report.unparsed.append("%s: %s" % (path.relative_to(root), exc))

    # Re-export facts: each module's import bindings, used to resolve
    # `from m import x` chains through facades back to the origin module.
    origins: dict[str, dict[str, set[str]]] = {}
    for module, tree in trees.items():
        if tree is None:
            continue
        collector = _UsageCollector(module, str(modules[module].relative_to(root)), None, report)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                collector.visit_Import(node)
            elif isinstance(node, ast.ImportFrom):
                collector.visit_ImportFrom(node)
        origins[module] = collector.bindings

    consumers = 0
    for module in sorted(modules):
        # The target's own modules consume themselves by definition; only
        # external consumers define the extraction surface.
        if module == target or module.startswith(target + "."):
            continue
        tree = trees[module]
        if tree is None:
            continue  # already recorded as unparsed
        collector = _UsageCollector(module, str(modules[module].relative_to(root)), target, report, origins=origins)
        # Pass 1: bindings (imports). Pass 2 must see assignments inside
        # function bodies too, so a plain two-visit order is not enough --
        # bindings are collected first, usages scanned after.
        before = len(report.usages)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                collector.visit_Import(node)
            elif isinstance(node, ast.ImportFrom):
                collector.visit_ImportFrom(node)
        collector._scan_usages(tree)
        if len(report.usages) > before:
            consumers += 1
    report.consumer_files = consumers
    return report
