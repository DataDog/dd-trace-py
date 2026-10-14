"""Command-line interface for the package-split harness.

Invoked by the thin ``scripts/package-split`` wrapper. The CLI owns manifest
loading/validation, gate dispatch, evidence writing and exit codes; gates
only mutate their GateReport.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional

from . import __version__
from .evidence import GateReport
from .gates import REGISTRY
from .manifest import ManifestError
from .manifest import load_manifest


DEFAULT_MANIFEST = "package-contract.toml"
DEFAULT_OUTPUT = ".package-split"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/package-split",
        description="Package-split validation gates (see docs/package-split/plan.md).",
    )
    parser.add_argument("--version", action="version", version="package-split harness %s" % __version__)
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help="package contract manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="directory for JSON evidence files (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="gate", metavar="<gate>")

    boundaries = subparsers.add_parser("boundaries", help="static import, dependency and build-metadata checks")
    boundaries.add_argument(
        "--dynamic-import-allowlist",
        action="append",
        default=[],
        help="fnmatch pattern for source locations allowed to use non-literal dynamic imports (repeatable)",
    )

    subparsers.add_parser("lifecycle", help="import inertness and activation correctness")
    subparsers.add_parser("artifacts", help="isolated builds and install checks")

    contracts = subparsers.add_parser("contracts", help="supported-API comparison against a baseline")
    contracts.add_argument("--baseline", required=True, help="path to the released baseline wheel")
    contracts.add_argument("--candidate", help="path to the candidate wheel (default: build one)")

    compatibility = subparsers.add_parser("compatibility", help="candidate/released version matrix")
    compatibility.add_argument("--matrix", required=True, help="path to the compatibility matrix TOML")
    compatibility.add_argument("--candidate", help="path to a prebuilt candidate wheel (default: build one)")

    migration = subparsers.add_parser("migration", help="old/new coexistence and rollback matrix")
    migration.add_argument("--matrix", required=True, help="path to the migration matrix TOML")
    migration.add_argument("--candidate", help="path to a prebuilt candidate wheel (default: build one)")

    freeze = subparsers.add_parser("freeze", help="protected source paths vs the extraction baseline")
    freeze.add_argument("--base", required=True, help="target revision to read the freeze manifest from")
    freeze.add_argument(
        "--head",
        default=None,
        help="revision whose drift is measured (default: HEAD; CI passes the PR head)",
    )
    freeze.add_argument("--freeze", help="freeze manifest path (default: package-freeze.toml)")

    api_usage = subparsers.add_parser(
        "api-usage",
        help="what other modules actually consume from a target module (used-API surface)",
    )
    api_usage.add_argument(
        "--module", required=True, help="dotted target module prefix, e.g. ddtrace.internal.coverage"
    )
    api_usage.add_argument(
        "--source-path",
        action="append",
        help=(
            "source path to scan for consumers (repeatable; default: the "
            "manifest's source paths, or --source-path when no manifest exists)"
        ),
    )

    api_surface = subparsers.add_parser(
        "api-surface",
        help="repo-wide consumed-symbol surfaces with bundling candidates per zone",
    )
    api_surface.add_argument(
        "--source-path",
        action="append",
        help=(
            "source path to scan (repeatable; default: the manifest's source "
            "paths, or --source-path when no manifest exists)"
        ),
    )
    api_surface.add_argument(
        "--zones",
        help="layers.json-style zone config for clustering (default: scripts/import-analysis/layers.json)",
    )
    api_surface.add_argument("--top", type=int, default=30, help="how many bundling candidates to print")

    subparsers.add_parser("gates", help="list the available gates")
    return parser


def _api_usage_command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Informational command: report the used-API surface of a module.

    Unlike gates this always exits 0 on success -- it is an inventory, not a
    pass/fail check. The manifest is optional: without one, --source-path is
    required to define the scan scope.
    """
    from pathlib import Path as _Path

    from .api_usage import analyze_api_usage

    manifest = None
    manifest_path = _Path(args.manifest)
    if args.source_path:
        root = _Path.cwd()
        source_paths = [root / p for p in args.source_path]
        import_roots = []
    elif manifest_path.is_file():
        from .manifest import load_manifest

        manifest = load_manifest(manifest_path)
        root = manifest.root
        source_paths = manifest.source_paths
        import_roots = manifest.import_roots
    else:
        print("package-split: no manifest at %s and no --source-path given" % manifest_path, file=sys.stderr)
        return 2

    try:
        report = analyze_api_usage(root, source_paths, import_roots, args.module)
    except Exception as exc:  # noqa: BLE001
        print("package-split: api-usage failed: %s" % exc, file=sys.stderr)
        return 2

    output_dir = _Path(args.output) / "api-usage"
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


def _api_surface_command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Informational command: repo-wide consumed-symbol surfaces.

    Always exits 0 on success -- it is an inventory, not a pass/fail check.
    """
    from .api_surface import analyze_api_surfaces
    from .api_surface import bundling_suggestions
    from .api_surface import summarize

    manifest = None
    manifest_path = Path(args.manifest)
    if args.source_path:
        root = Path.cwd()
        source_paths = [root / p for p in args.source_path]
        import_roots: list[str] = []
    elif manifest_path.is_file():
        from .manifest import load_manifest

        manifest = load_manifest(manifest_path)
        root = manifest.root
        source_paths = manifest.source_paths
        import_roots = manifest.import_roots
    else:
        print("package-split: no manifest at %s and no --source-path given" % manifest_path, file=sys.stderr)
        return 2

    if not import_roots:
        # Without a manifest, derive the import root from the first source path.
        import_roots = [source_paths[0].name]

    zones = None
    zones_path = Path(args.zones) if args.zones else root / "scripts" / "import-analysis" / "layers.json"
    if zones_path.is_file():
        import json as _json

        zones = _json.loads(zones_path.read_text(encoding="utf-8")).get("zones")

    try:
        report = analyze_api_surfaces(root, source_paths, import_roots, zones)
    except Exception as exc:  # noqa: BLE001
        print("package-split: api-surface failed: %s" % exc, file=sys.stderr)
        return 2

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
    args = parser.parse_args(argv if argv is not None else None)

    if args.gate is None:
        parser.print_help(sys.stderr)
        return 2
    if args.gate == "gates":
        print("\n".join(sorted(REGISTRY)))
        return 0
    if args.gate == "api-usage":
        return _api_usage_command(parser, args)
    if args.gate == "api-surface":
        return _api_surface_command(parser, args)

    gate = args.gate
    manifest_path = Path(args.manifest)
    output_dir = Path(args.output)

    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        print("package-split: %s" % exc, file=sys.stderr)
        return 2

    report = GateReport(gate, manifest_path, __version__)
    try:
        REGISTRY[gate](manifest, args, report)
    except Exception as exc:  # noqa: BLE001 - crashes are reported as gate errors
        report.error = "%s: %s" % (type(exc).__name__, exc)
    evidence = report.finish(output_dir / gate)
    print(report.summary())
    print("evidence: %s" % evidence)
    return report.exit_code
