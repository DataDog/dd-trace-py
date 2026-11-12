"""Command-line interface for the package-split API inventories.

Invoked by the thin ``scripts/package-split`` wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Optional

from .api_usage import module_names_for


DEFAULT_OUTPUT = ".package-split"
DEFAULT_SOURCE_PATHS = ["ddtrace"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/package-split",
        description="Static API inventories for choosing package-split boundaries.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="directory for JSON evidence files (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    source_path_help = "source path to scan, relative to the current directory (repeatable; default: ddtrace)"

    api_usage = subparsers.add_parser(
        "api-usage",
        help="what other modules actually consume from a target module (used-API surface)",
    )
    api_usage.add_argument(
        "--module", required=True, help="dotted target module prefix, e.g. ddtrace.internal.coverage"
    )
    api_usage.add_argument("--source-path", action="append", help=source_path_help)
    api_usage.add_argument(
        "--allow-absent-target",
        action="store_true",
        help="scan a consumer-only scope (e.g. tests/) whose source paths do not contain the target module",
    )

    api_surface = subparsers.add_parser(
        "api-surface",
        help="repo-wide consumed-symbol surfaces with bundling candidates per zone",
    )
    api_surface.add_argument("--source-path", action="append", help=source_path_help)
    api_surface.add_argument(
        "--zones",
        help="layers.json-style zone config for clustering (default: scripts/import-analysis/layers.json)",
    )
    api_surface.add_argument("--top", type=int, default=30, help="how many bundling candidates to print")

    for command in subparsers.choices.values():
        command.add_argument("--output", default=argparse.SUPPRESS)
    return parser


def _inventory_inputs(args: argparse.Namespace) -> tuple[Path, list[Path], list[str], list[str]]:
    """Scan root, source paths, import roots and the raw source-path args.

    Import roots are the top-level names of the scanned modules; the raw
    args are kept so evidence paths can encode the scan scope.
    """
    root = Path.cwd()
    raw_source_paths = list(args.source_path or DEFAULT_SOURCE_PATHS)
    source_paths = [root / p for p in raw_source_paths]
    import_roots = sorted({module.split(".", 1)[0] for module in module_names_for(root, source_paths)})
    return root, source_paths, import_roots, raw_source_paths


def _scope_dir(raw_source_paths: list[str]) -> str:
    """Deterministic evidence subdirectory for a scan scope.

    The port workflow inventories the same target over several scopes
    (production, then tests); a shared evidence path silently overwrote
    earlier runs, so the scope -- the source paths -- is part of the path.
    """

    def clean(value: str) -> str:
        value = value.strip("./")
        return "_".join(value.split("/")) if value else "root"

    label = "__".join(clean(path) for path in raw_source_paths)[:100]
    digest = hashlib.sha256(json.dumps(raw_source_paths, ensure_ascii=True).encode("utf-8")).hexdigest()
    return "%s-%s" % (label, digest)


def _warn_unparsed(unparsed: list[str]) -> None:
    if unparsed:
        print("package-split: skipped %d unparsable file(s):" % len(unparsed), file=sys.stderr)
        for entry in unparsed:
            print("  %s" % entry, file=sys.stderr)


def _api_usage_command(args: argparse.Namespace) -> int:
    """Informational command: report the used-API surface of a module.

    Always exits 0 on success -- it is an inventory, not a pass/fail check.
    """
    from .api_usage import analyze_api_usage

    if not all(part.isidentifier() for part in args.module.split(".")):
        raise ValueError("target must be a dotted module name: %s" % args.module)
    root, source_paths, _, raw_source_paths = _inventory_inputs(args)
    report = analyze_api_usage(root, source_paths, args.module, require_target=not args.allow_absent_target)
    # The target module is part of the evidence path: two different
    # --module values with the same source-path scope would otherwise
    # write the same file, silently overwriting each other's results.
    output_dir = Path(args.output) / "api-usage" / _scope_dir(raw_source_paths) / args.module
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "api-usage.json"
    evidence.write_text(
        json.dumps(
            {
                "target": report.target,
                "source_paths": raw_source_paths,
                "files_scanned": report.files_scanned,
                "consumer_files": report.consumer_files,
                "unparsed": report.unparsed,
                "symbols": report.symbols(),
                "usages": [usage.__dict__ for usage in report.usages],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(report.summary())
    _warn_unparsed(report.unparsed)
    print("evidence: %s" % evidence)
    return 0


def _api_surface_command(args: argparse.Namespace) -> int:
    """Informational command: repo-wide consumed-symbol surfaces.

    Always exits 0 on success -- it is an inventory, not a pass/fail check.
    """
    from .api_surface import analyze_api_surfaces
    from .api_surface import bundling_suggestions
    from .api_surface import summarize

    root, source_paths, import_roots, raw_source_paths = _inventory_inputs(args)

    zones = None
    # An explicit --zones path must exist; only the default is optional.
    zones_path = Path(args.zones) if args.zones else root / "scripts" / "import-analysis" / "layers.json"
    if args.zones:
        # A user-supplied path is an input, not an optional default: a
        # misspelled or missing file is an error, not a silently empty
        # clustering pass.
        if not zones_path.is_file():
            raise ValueError("zones file not found: %s" % zones_path)
        data = json.loads(zones_path.read_text(encoding="utf-8"))
        zones = data.get("zones")
    elif zones_path.is_file():
        zones = json.loads(zones_path.read_text(encoding="utf-8")).get("zones")
    if zones_path.is_file():
        if not isinstance(zones, dict):
            raise ValueError("%s has no 'zones' mapping" % zones_path)
        if not all(isinstance(prefix, str) and isinstance(zone, str) for prefix, zone in zones.items()):
            raise ValueError("%s zones must map strings to strings" % zones_path)

    report = analyze_api_surfaces(root, source_paths, import_roots, zones)

    suggestions = bundling_suggestions(report)
    output_dir = Path(args.output) / "api-surface" / _scope_dir(raw_source_paths)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "api-surface.json"
    evidence.write_text(
        json.dumps(
            {
                "source_paths": raw_source_paths,
                "report": report.to_dict(),
                "zones": report.zones,
                "bundling_suggestions": [s.to_dict() for s in suggestions],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(summarize(report, suggestions, top=args.top))
    _warn_unparsed(report.unparsed)
    print("evidence: %s" % evidence)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return _api_usage_command(args) if args.command == "api-usage" else _api_surface_command(args)
    except (OSError, ValueError, SyntaxError) as exc:
        print("package-split: %s failed: %s" % (args.command, exc), file=sys.stderr)
        return 2
