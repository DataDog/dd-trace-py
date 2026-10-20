#!/usr/bin/env python3
"""Automate running tach (https://github.com/tach-org/tach) against dd-trace-py.

`tach init` requires a real TTY for its interactive module picker, which makes it
unusable headless. This script replaces that step: it generates tach.toml
directly (one module per top-level package under ddtrace/), then uses the
non-interactive `tach sync --add` to populate module dependencies from actual
imports. After that, `tach check` guards the declared boundaries.

Typical flow, from repo root (or a worktree of it):

    uv run scripts/run_tach.py               # full run: init if needed, sync, check, artifacts
    uv run scripts/run_tach.py --force       # regenerate tach.toml skeleton from scratch
    uv run scripts/run_tach.py --no-artifacts # skip the mermaid graph / report generation
    uv run scripts/run_tach.py --check-only   # only run tach check (CI-style gate)

tach itself is executed via `uvx` (no install). Pass --tach-version to pin it
for reproducible results.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess  # nosec: B404
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = "ddtrace"

# Paths tach should not scan. These match the ones the hand-written config used;
# keep vendor out since it is third-party code.
DEFAULT_EXCLUDES = [
    "tests",
    "benchmarks",
    "build",
    f"{MODULES_DIR}/vendor",
    ".riot",
    ".gitlab",
    ".github",
    "docs",
    "scripts",
    "hooks",
    "templates",
]

CONFIG_HEADER = """\
# Module boundaries for tach (https://github.com/tach-org/tach)
#
# Modules are defined at the top-level package boundaries of ddtrace/.
# depends_on lists are maintained by `tach sync --add` (run scripts/run_tach.py)
# to reflect actual dependencies.
"""

# Vendored third-party code: excluded from module boundaries and scanning.
# Explicitly defining a module would override the exclude list, so it must be
# skipped at generation time too.
EXCLUDED_PACKAGES = {"vendor"}


def find_module_paths() -> list[str]:
    """Return dotted module paths: the root package plus each top-level
    subpackage that has an __init__.py. Files and caches are ignored.
    """
    modules = [MODULES_DIR]
    modules_dir = PROJECT_ROOT / MODULES_DIR
    if not modules_dir.is_dir():
        sys.exit(f"error: {modules_dir} not found (run this from the repo root)")
    for entry in sorted(modules_dir.iterdir()):
        if entry.is_dir() and (entry / "__init__.py").is_file() and entry.name not in EXCLUDED_PACKAGES:
            modules.append(f"{MODULES_DIR}.{entry.name}")
    return modules


def write_config(force: bool, excludes: list[str]) -> None:
    """Write the tach.toml skeleton if it does not exist yet (or with --force).

    If tach.toml already exists it is left untouched: it holds depends_on data
    produced by `tach sync --add` that we do not want to throw away.
    """
    config_path = PROJECT_ROOT / "tach.toml"
    if config_path.exists() and not force:
        print("tach.toml already exists, keeping it (use --force to regenerate)")
        return
    modules = find_module_paths()
    lines = [CONFIG_HEADER, f"exclude = {excludes!r}".replace("'", '"'), ""]
    for module in modules:
        lines.append("[[modules]]")
        lines.append(f'path = "{module}"')
        lines.append("")
    config_path.write_text("\n".join(lines))
    print(f"wrote {config_path} with {len(modules)} modules")


def run_tach(tach_version: str | None, *args: str) -> None:
    """Run a tach subcommand through uvx, exiting on failure."""
    cmd = ["uvx"] + ([f"tach=={tach_version}"] if tach_version else ["tach"]) + list(args)
    print(f"+ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)  # nosec: B603, B607
    if result.returncode != 0:
        sys.exit(f"error: `{' '.join(args)}` failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="regenerate tach.toml even if it exists")
    parser.add_argument("--check-only", action="store_true", help="only run tach check")
    parser.add_argument("--no-artifacts", action="store_true", help="skip mermaid graph and report generation")
    parser.add_argument("--tach-version", help="pin the tach version, e.g. 0.35.3")
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=DEFAULT_EXCLUDES,
        help="paths tach should not scan (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.check_only:
        run_tach(args.tach_version, "check")
        return

    # 1. Boundaries: hand-written config replaces the TTY-only `tach init`.
    write_config(args.force, args.exclude)

    # 2. Dependencies: sync is non-interactive and --add only ever adds
    #    constraints, so it is safe to re-run repeatedly.
    run_tach(args.tach_version, "sync", "--add")

    # 3. Validate boundaries against actual imports.
    run_tach(args.tach_version, "check")

    if not args.no_artifacts:
        run_tach(args.tach_version, "show", "--mermaid", "-o", "tach_module_graph.mmd")
        # export records git commit metadata and refuses to run on a dirty tree;
        # --force accepts that the report may reference the last commit instead.
        run_tach(args.tach_version, "export", "-f", "-o", "tach_modularity_report.json")


if __name__ == "__main__":
    main()
