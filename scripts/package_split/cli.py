"""Command-line interface for the package-split harness.

Invoked by the thin ``scripts/package-split`` wrapper. The CLI owns manifest
loading/validation, gate dispatch, evidence writing and exit codes; gates
only mutate their GateReport.

Freeze-first note: this repo currently registers only the source-side
``freeze`` gate -- the frozen baseline is established on main before any
extraction lands. The remaining gates and inventories live on the
package-split branches and are reviewed in separately.
"""

from __future__ import annotations

import argparse
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

    freeze = subparsers.add_parser("freeze", help="protected source paths vs the extraction baseline")
    freeze.add_argument("--base", required=True, help="target revision to read the freeze manifest from")
    freeze.add_argument(
        "--head",
        default=None,
        help="revision whose drift is measured (default: HEAD; CI passes the PR head)",
    )
    freeze.add_argument("--freeze", help="freeze manifest path (default: package-freeze.toml)")

    subparsers.add_parser("gates", help="list the available gates")
    for command in subparsers.choices.values():
        command.add_argument("--manifest", default=argparse.SUPPRESS)
        command.add_argument("--output", default=argparse.SUPPRESS)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else None)

    if args.gate is None:
        parser.print_help(sys.stderr)
        return 2
    if args.gate == "gates":
        print("\n".join(sorted(REGISTRY)))
        return 0

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
