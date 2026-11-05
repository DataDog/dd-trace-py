"""Command-line interface for the package-split API inventories.

Invoked by the thin ``scripts/package-split`` wrapper.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional


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


def _inventory_inputs(args: argparse.Namespace) -> tuple[Path, list[Path], list[str]]:
    """Scan root, source paths and import roots (the source paths' top-level names)."""
    root = Path.cwd()
    source_paths = [root / p for p in args.source_path or DEFAULT_SOURCE_PATHS]
    import_roots = list(dict.fromkeys(p.relative_to(root).parts[0] for p in source_paths))
    return root, source_paths, import_roots


def _api_usage_command(args: argparse.Namespace) -> int:
    """Informational command: report the used-API surface of a module.

    Always exits 0 on success -- it is an inventory, not a pass/fail check.
    """
    from .api_usage import analyze_api_usage

    root, source_paths, _ = _inventory_inputs(args)
    report = analyze_api_usage(root, source_paths, args.module)
    output_dir = Path(args.output) / "api-usage"
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "api-usage.json"
    evidence.write_text(
        json.dumps(
            {
                "target": report.target,
                "files_scanned": report.files_scanned,
                "consumer_files": report.consumer_files,
                "symbols": report.symbols(),
                "usages": [usage.__dict__ for usage in report.usages],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(report.summary())
    print("evidence: %s" % evidence)
    return 0


def _api_surface_command(args: argparse.Namespace) -> int:
    """Informational command: repo-wide consumed-symbol surfaces.

    Always exits 0 on success -- it is an inventory, not a pass/fail check.
    """
    from .api_surface import analyze_api_surfaces
    from .api_surface import bundling_suggestions
    from .api_surface import summarize

    root, source_paths, import_roots = _inventory_inputs(args)

    zones = None
    zones_path = Path(args.zones) if args.zones else root / "scripts" / "import-analysis" / "layers.json"
    if zones_path.is_file():
        zones = json.loads(zones_path.read_text(encoding="utf-8")).get("zones")

    report = analyze_api_surfaces(root, source_paths, import_roots, zones)

    suggestions = bundling_suggestions(report)
    output_dir = Path(args.output) / "api-surface"
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "api-surface.json"
    evidence.write_text(
        json.dumps(
            {
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
