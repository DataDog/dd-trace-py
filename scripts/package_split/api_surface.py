"""Repo-wide API surface clustering: who consumes what, grouped by zone.

Answers the package-split question one level up from api-usage: for every
module in the repository, which of its symbols do other modules consume, and
how concentrated is that consumption? The bundling heuristic mirrors the
migration reasoning:

- a symbol (or module) whose consumers all live in one other zone is a
  candidate to be bundled with that zone's package rather than kept in a
  shared foundation;
- symbols consumed across several zones are genuinely shared surface and
  must survive extraction into the foundation;
- symbols with no external consumers can stay private.

Zones come from a layers.json-style config (``{"zones": {prefix: zone}}``,
longest prefix wins; unclassified modules are "foundation"), reusing
scripts/import-analysis/layers.json when present so consumer clustering
matches the repository's own architectural vocabulary.

The analysis is one static pass: every import binding in every module is
resolved and every use site classified (as in api_usage), then each usage is
attributed to the *owning* module -- the longest module prefix of the
resolved path that exists in the module index.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from .api_usage import ApiUsageReport
from .api_usage import Usage
from .api_usage import _UsageCollector
from .api_usage import module_names_for
from .api_usage import parse_source


FOUNDATION_ZONE = "foundation"


@dataclass
class OwnerSurface:
    """The externally consumed symbols of one owning module."""

    module: str
    # symbol (relative to the owner) -> consumer module -> usage kinds
    symbols: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def consumers_of(self, symbol: str) -> set[str]:
        return set(self.symbols.get(symbol, {}))

    @property
    def external_symbols(self) -> set[str]:
        return set(self.symbols)

    def consumer_zones(self, symbol: str, zones: dict[str, str]) -> set[str]:
        return {zone_of(consumer, zones) for consumer in self.consumers_of(symbol)}


@dataclass
class ApiSurfaceReport:
    roots: list[str]
    zones: dict[str, str]
    owners: dict[str, OwnerSurface] = field(default_factory=dict)
    files_scanned: int = 0
    unparsed: list[str] = field(default_factory=list)
    raw_usages: list[Usage] = field(default_factory=list)
    # Owner module -> its package root (the extraction unit that moves
    # wholesale; consumers under it relocate with the package).
    package_roots: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "roots": self.roots,
            "files_scanned": self.files_scanned,
            "unparsed": self.unparsed,
            "owners": {
                module: {
                    "symbols": {
                        symbol: {consumer: kinds for consumer, kinds in per_consumer.items()}
                        for symbol, per_consumer in surface.symbols.items()
                    }
                }
                for module, surface in self.owners.items()
            },
        }


def zone_of(module: str, zones: dict[str, str]) -> str:
    """Longest-prefix zone lookup; unclassified modules are foundation."""
    best: tuple[int, str] | None = None
    for prefix, zone in zones.items():
        if module == prefix or module.startswith(prefix + "."):
            length = len(prefix)
            if best is None or length > best[0]:
                best = (length, zone)
    return best[1] if best is not None else FOUNDATION_ZONE


def _default_zones(roots: Iterable[str]) -> dict[str, str]:
    """Unclassified roots belong to the foundation zone."""
    return dict.fromkeys(roots, FOUNDATION_ZONE)


def _package_root(module: str, modules: dict[str, Path]) -> str:
    """The package a module belongs to (itself for packages).

    ``shared/base.py`` -> ``shared``; ``shared/sub/__init__.py`` ->
    ``shared.sub``. Used as the bundling unit: consumers inside the owner's
    own package move with it during extraction, so they do not block
    bundling suggestions.
    """
    path = modules.get(module)
    if path is None:
        return module
    if path.name == "__init__.py":
        return module
    parent = module.rsplit(".", 1)[0]
    if parent in modules and modules[parent].name == "__init__.py":
        return parent
    return module


def analyze_api_surfaces(
    root: Path,
    source_paths: Iterable[Path],
    import_roots: list[str],
    zones: dict[str, str] | None = None,
) -> ApiSurfaceReport:
    """Single static pass computing per-owner consumed-symbol surfaces."""
    module_paths = module_names_for(root, source_paths)
    modules = set(module_paths)
    report = ApiSurfaceReport(
        roots=list(import_roots),
        zones=zones if zones is not None else _default_zones(import_roots),
    )
    report.files_scanned = len(modules)

    # Reuse the api-usage machinery in surface mode (target=None): the sink
    # collects every usage rooted at the import roots, attribution happens
    # afterwards.
    sink = ApiUsageReport(target="<surfaces>")
    # Package -> name bound in its __init__ -> modules or symbols it re-exports.
    reexports: dict[str, dict[str, set[str]]] = {}
    for module, path in sorted(module_paths.items()):
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            report.unparsed.append("%s: %s" % (path.relative_to(root), exc))
            continue
        collector = _UsageCollector(module, str(path.relative_to(root)), None, sink, roots=set(import_roots))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                collector.visit_Import(node)
            elif isinstance(node, ast.ImportFrom):
                collector.visit_ImportFrom(node)
        collector._scan_usages(tree)
        if path.name == "__init__.py":
            reexports[module] = collector.bindings

    # Attribute each usage to its owning module: the longest module prefix
    # of the resolved path that exists in the module index. A use through a
    # package re-export is also attributed to the module it re-exports, so
    # the implementation does not look private.
    for usage in sink.usages:
        for owner, symbol in _attributions(usage.symbol, modules, import_roots, reexports):
            surface = report.owners.setdefault(owner, OwnerSurface(module=owner))
            per_consumer = surface.symbols.setdefault(symbol, {})
            per_consumer.setdefault(usage.importer, []).append(usage.kind)
        report.raw_usages.append(usage)
    for module in report.owners:
        report.package_roots[module] = _package_root(module, module_paths)
    return report


def _attributions(
    resolved: str,
    modules: set[str],
    import_roots: list[str],
    reexports: dict[str, dict[str, set[str]]],
) -> list[tuple[str, str]]:
    """(owner, symbol relative to owner) pairs for a resolved path, following re-exports."""
    attributions: list[tuple[str, str]] = []
    pending = [resolved]
    seen: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        owner = _owning_module(path, modules, import_roots)
        if owner is None:
            continue
        if path == owner:
            attributions.append((owner, "<module>"))
            continue
        rest = path[len(owner) + 1 :]
        attributions.append((owner, rest))
        name, _, tail = rest.partition(".")
        for target in reexports.get(owner, {}).get(name, ()):
            if target != path:
                pending.append(target + "." + tail if tail else target)
    return attributions


def _owning_module(resolved: str, modules: set[str], import_roots: list[str]) -> str | None:
    if resolved.split(".")[0] not in import_roots:
        return None
    parts = resolved.split(".")
    # Submodule imports and symbols are indistinguishable statically; walk
    # from the longest candidate down. A symbol that also names a submodule
    # is attributed to the deeper module (safe over-approximation).
    for depth in range(len(parts), 0, -1):
        candidate = ".".join(parts[:depth])
        if candidate in modules:
            return candidate
    return None


@dataclass
class BundlingSuggestion:
    owner: str
    owner_zone: str
    consumer_zone: str
    symbols: list[str]
    single_consumer_file: str | None  # set when every symbol has one consumer file

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "owner_zone": self.owner_zone,
            "consumer_zone": self.consumer_zone,
            "symbols": self.symbols,
            "single_consumer_file": self.single_consumer_file,
        }


def _is_within(module: str, owner: str) -> bool:
    return module == owner or module.startswith(owner + ".")


def bundling_suggestions(
    report: ApiSurfaceReport,
    min_symbols: int = 1,
) -> list[BundlingSuggestion]:
    """Heuristic bundling candidates.

    A symbol suggests bundling when *all* of its consumers (excluding those
    inside the owner's own package, which move with it) live in a single
    zone different from the owner's. Modules where most symbols point at
    the same zone are strong candidates to move wholesale (or to expose
    only the remaining shared symbols from a foundation package).
    """
    suggestions: list[BundlingSuggestion] = []
    for owner, surface in sorted(report.owners.items()):
        owner_zone = zone_of(owner, report.zones)
        # Consumers within the owner's own package move with it during
        # extraction and therefore do not block bundling.
        package_root = report.package_roots.get(owner, owner)
        by_zone: dict[str, list[str]] = {}
        single_file: dict[str, set[str]] = {}
        for symbol in sorted(surface.symbols):
            # Consumers in a differently zoned subpackage do not move with the owner.
            consumers = [
                consumer
                for consumer in surface.consumers_of(symbol)
                if not _is_within(consumer, package_root) or zone_of(consumer, report.zones) != owner_zone
            ]
            if not consumers:
                # No external consumers: the symbol can stay private to the
                # owner's package.
                continue
            consumer_zones = {zone_of(consumer, report.zones) for consumer in consumers}
            if len(consumer_zones) == 1:
                zone = next(iter(consumer_zones))
                if zone != owner_zone:
                    by_zone.setdefault(zone, []).append(symbol)
                    single_file.setdefault(zone, set()).update(consumers)
        for zone, symbols in sorted(by_zone.items()):
            if len(symbols) >= min_symbols:
                files = single_file[zone]
                suggestions.append(
                    BundlingSuggestion(
                        owner=owner,
                        owner_zone=owner_zone,
                        consumer_zone=zone,
                        symbols=symbols,
                        single_consumer_file=next(iter(files)) if len(files) == 1 else None,
                    )
                )
    # Strongest signals first: most bundleable symbols, then single-file.
    suggestions.sort(key=lambda s: (-len(s.symbols), s.single_consumer_file is None, s.owner))
    return suggestions


def summarize(report: ApiSurfaceReport, suggestions: list[BundlingSuggestion], top: int = 30) -> str:
    lines = [
        "api-surface: %d owning module(s) with external consumers, %d file(s) scanned"
        % (len(report.owners), report.files_scanned)
    ]
    lines.append("")
    lines.append("bundling candidates (symbols whose consumers all sit in one other zone):")
    for suggestion in suggestions[:top]:
        single = (
            " [single consumer file: %s]" % suggestion.single_consumer_file if suggestion.single_consumer_file else ""
        )
        lines.append(
            "  %-55s %s -> %s (%d symbol(s))%s"
            % (
                suggestion.owner,
                suggestion.owner_zone,
                suggestion.consumer_zone,
                len(suggestion.symbols),
                single,
            )
        )
        for symbol in suggestion.symbols[:8]:
            lines.append("      . %s" % symbol)
        if len(suggestion.symbols) > 8:
            lines.append("      ... %d more" % (len(suggestion.symbols) - 8))
    return "\n".join(lines)
