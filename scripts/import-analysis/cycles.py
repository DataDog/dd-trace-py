# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "betsy @ git+https://github.com/p403n1x87/betsy.git",
# ]
# ///
from argparse import ArgumentParser
from collections import deque
import json
from pathlib import Path
import sys

from betsy import DependencyGraph


def _build_graph(root: Path) -> dict:
    g = DependencyGraph(root=root.resolve(), include={"ddtrace"}).data
    q: deque[str] = deque()
    for importer, imports in list(g.items()):
        if not imports:
            q.append(importer)
            del g[importer]
    return g


def dfs(v: str, visited: set[str], stack: list[str], cycles: dict[frozenset, tuple], g: dict):
    for i in g.get(v, set()):
        if i not in visited:
            dfs(i, {*visited, v}, [*stack, v], cycles, g)
        else:
            if i in stack:
                cycle = tuple(stack[stack.index(i) :] + [v, i])
                if cycle not in cycles:
                    cycles[frozenset(cycle)] = cycle


def analyze(args):
    root = args.root if args.root is not None else Path(__file__).parents[2] / "ddtrace"
    g = _build_graph(root)

    dfs("ddtrace", set(), [], cycles := {}, g)

    res = ",\n".join(json.dumps(lst) for lst in sorted(cycles.values(), key=len))
    args.output.write_text(f"[\n{res}\n]")

    if cycles:
        print(f"Detected {len(cycles)} circular imports.")


def compare(args):
    def to_dict(path: Path) -> dict[frozenset, tuple]:
        return {frozenset(cycle): cycle for cycle in json.loads(path.read_text())}

    base, pr = map(to_dict, [args.base, args.pr])

    def print_cycles(cycles: list[tuple]):
        print("```")
        for cycle in cycles:
            print(" -> ".join(cycle))
        print("```")
        print()

    new_cycles = pr.keys() - base.keys()
    removed_cycles = base.keys() - pr.keys()
    existing_cycles = base.keys() & pr.keys()

    if new_cycles:
        print("## 🚨 New circular imports detected 🚨")
        print()
        print(
            "The following circular imports among modules have been detected on "
            "this PR, when compared to the base branch:"
        )
        print()
        print_cycles([pr[_] for _ in new_cycles])
        print(
            "Please consider refactoring your changes in accordance to the "
            "[Separation of Concerns](https://en.wikipedia.org/wiki/Separation_of_concerns) principle."
        )
        print()

    if existing_cycles:
        print("## ⚠️ Existing circular imports")
        print()
        print("The following circular imports already exist on the base branch and have not been changed by this PR:")
        print()
        print_cycles([pr[_] for _ in existing_cycles])

    if removed_cycles:
        print("## ✅ Circular imports removed")
        print()
        print("The following circular imports have been removed on this PR, when compared to the base branch:")
        print()
        print_cycles([base[_] for _ in removed_cycles])

    return bool(new_cycles)


def main() -> bool:
    argp = ArgumentParser()

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

    return globals()[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
