# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "betsy @ git+https://github.com/p403n1x87/betsy.git",
# ]
# ///
import argparse
from collections import deque
import json
from pathlib import Path
import sys

from betsy import DependencyGraph


_Cycle = tuple[str, ...]
_CycleMap = dict[frozenset[str], _Cycle]


def _build_graph(root: Path) -> dict[str, set[str]]:
    g: dict[str, set[str]] = DependencyGraph(root=root.resolve(), include={"ddtrace"}).data
    q: deque[str] = deque()
    for importer, imports in list(g.items()):
        if not imports:
            q.append(importer)
            del g[importer]
    return g


def dfs(v: str, visited: set[str], stack: list[str], cycles: _CycleMap, g: dict[str, set[str]]) -> None:
    for i in g.get(v, set()):
        if i not in visited:
            dfs(i, {*visited, v}, [*stack, v], cycles, g)
        elif i in stack:
            cycle: _Cycle = tuple(stack[stack.index(i) :] + [v, i])
            key = frozenset(cycle)
            if key not in cycles:
                cycles[key] = cycle


def analyze(args: argparse.Namespace) -> None:
    root = args.root if args.root is not None else Path(__file__).parents[2] / "ddtrace"
    g = _build_graph(root)

    cycles: _CycleMap = {}
    dfs("ddtrace", set(), [], cycles, g)

    res = ",\n".join(json.dumps(lst) for lst in sorted(cycles.values(), key=len))
    args.output.write_text(f"[\n{res}\n]")

    if cycles:
        print(f"Detected {len(cycles)} circular imports.")


_PREVIEW = 5  # max cycles shown inline per section before collapsing into <details>

_ARTIFACTS_HINT = (
    "> To see all cycles, download the `cycles-base.json` and `cycles-pr.json` artifacts "
    "from this CI job and run:\n"
    "> ```\n"
    "> uv run --script scripts/import-analysis/cycles.py compare cycles-base.json cycles-pr.json\n"
    "> ```"
)


def compare(args: argparse.Namespace) -> bool:
    def to_dict(path: Path) -> _CycleMap:
        return {frozenset(cycle): tuple(cycle) for cycle in json.loads(path.read_text())}

    base, pr = map(to_dict, [args.base, args.pr])

    def print_cycles(cycles: list[_Cycle]) -> None:
        print("```")
        for cycle in cycles:
            print(" -> ".join(cycle))
        print("```")
        print()

    def print_capped(cycles: list[_Cycle], summary: str) -> None:
        """Print up to _PREVIEW cycles inline; collapse the rest into a <details> block."""
        if len(cycles) <= _PREVIEW:
            print_cycles(cycles)
        else:
            print(f"<details><summary>{summary} (showing {_PREVIEW} of {len(cycles)} shortest)</summary>")
            print()
            print_cycles(cycles[:_PREVIEW])
            print("</details>")
            print()
            print(_ARTIFACTS_HINT)
        print()

    new_cycles = pr.keys() - base.keys()
    removed_cycles = base.keys() - pr.keys()
    existing_cycles = base.keys() & pr.keys()

    if new_cycles:
        sorted_new = sorted([pr[_] for _ in new_cycles], key=len)
        print("## 🚨 New circular imports detected 🚨")
        print()
        print(f"**{len(new_cycles)}** new circular import(s) have been introduced by this PR:")
        print()
        print_capped(sorted_new, "Show new cycles")
        print(
            "Please consider refactoring your changes in accordance to the "
            "[Separation of Concerns](https://en.wikipedia.org/wiki/Separation_of_concerns) principle."
        )
        print()

    if existing_cycles:
        sorted_existing = sorted([pr[_] for _ in existing_cycles], key=len)
        print("## ⚠️ Existing circular imports")
        print()
        print(
            f"There are **{len(existing_cycles)}** circular imports that already exist on the base branch "
            "and have not been changed by this PR."
        )
        print()
        print_capped(sorted_existing, "Show existing cycles")

    if removed_cycles:
        sorted_removed = sorted([base[_] for _ in removed_cycles], key=len)
        print("## ✅ Circular imports removed")
        print()
        print(f"**{len(removed_cycles)}** circular import(s) have been removed by this PR.")
        print()
        print_capped(sorted_removed, "Show removed cycles")

    return bool(new_cycles)


def main() -> int:
    argp = argparse.ArgumentParser()

    subp = argp.add_subparsers(dest="command")

    subp_analyze = subp.add_parser("analyze")
    subp_analyze.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Path to the ddtrace package root (default: auto-detected from __file__)",
    )
    subp_analyze.add_argument("output", type=Path)

    subp_compare = subp.add_parser("compare")
    subp_compare.add_argument("base", type=Path)
    subp_compare.add_argument("pr", type=Path)

    args = argp.parse_args()

    return int(globals()[args.command](args) or 0)


if __name__ == "__main__":
    sys.exit(main())
