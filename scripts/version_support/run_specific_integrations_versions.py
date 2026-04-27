"""Rewrite selected Riot integration suites from high-level matrix specs.

This helper parses `riotfile.py`, regenerates targeted integration subtrees
from compact version-support specs, and writes the rewritten Riot config back
to disk.
"""

import argparse
from pathlib import Path

from .json_specs import load_specs_from_json
from .json_specs import load_specs_from_json_text
from .models import FallbackSpec
from .models import IntegrationSpec
from .parsing import collect_venvs
from .rewriting import regenerate_suite


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec-file",
        type=Path,
        default=Path(__file__).resolve().parent / "specs.json",
        help="Path to JSON matrix specs file (default: scripts/version_support/specs.json)",
    )
    parser.add_argument(
        "--spec-json",
        help="Raw JSON matrix specs. Takes precedence over --spec-file.",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    riotfile_path = (Path(__file__).resolve().parent.parent.parent / "riotfile.py").resolve()

    riotfile_source: str = riotfile_path.read_text(encoding="utf-8")
    riotfile_filename = str(riotfile_path)
    suites = collect_venvs(riotfile_source, riotfile_filename)
    if args.spec_json:
        specs = load_specs_from_json_text(args.spec_json, suites=suites)
    else:
        specs = load_specs_from_json(args.spec_file.resolve(), suites=suites)

    rewritten_source = riotfile_source
    rewritten_suites: list[str] = []
    skipped_suites: list[str] = []
    for suite_name, spec in specs.items():
        suite = suites.get(suite_name)
        if suite is None:
            raise RuntimeError(f"Unable to find suite {suite_name!r} in riotfile.py")

        if isinstance(spec, FallbackSpec):
            skipped_suites.append(f"{suite_name} ({spec.reason})")
            continue
        if not isinstance(spec, IntegrationSpec):
            raise RuntimeError(f"Unsupported spec type for {suite_name!r}: {type(spec)!r}")

        rewritten_source = regenerate_suite(
            rewritten_source,
            suite=suite,
            integration_spec=spec,
        )
        rewritten_suites.append(suite_name)
        suites = collect_venvs(rewritten_source, str(riotfile_path))

    riotfile_path.write_text(rewritten_source, encoding="utf-8")
    if rewritten_suites:
        print(f"Rewrote suites: {', '.join(rewritten_suites)}")
    if skipped_suites:
        print(f"Skipped fallback suites: {', '.join(skipped_suites)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
