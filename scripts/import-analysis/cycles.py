from argparse import ArgumentParser
from collections import defaultdict
from collections import deque
import json
from pathlib import Path
import sys
import typing as t

from betsy import DependencyGraph


ROOT = Path(__file__).parents[2] / "ddtrace"


g = DependencyGraph(root=ROOT, include={"ddtrace"}).data  # modules and what they import
q: t.Deque[str] = deque()
f = defaultdict(set)  # modules and who imports them
for importer, imports in list(g.items()):
    if not imports:
        q.append(importer)
        del g[importer]
    else:
        for i in imports:
            f[i].add(importer)
            print(f"{importer} -> {i}")


def dfs(v: str, visited: t.Set[str], stack: t.List[str], cycles: dict[frozenset, tuple]):
    for i in g.get(v, set()):
        if i not in visited:
            dfs(i, {*visited, v}, [*stack, v], cycles)
        else:
            if i in stack:
                cycle = tuple(stack[stack.index(i) :] + [v, i])
                if cycle not in cycles:
                    cycles[frozenset(cycle)] = cycle


def analyze(args):
    dfs("ddtrace", set(), [], cycles := {})

    def key(cycle):
        return len(cycle), cycle

    res = ",\n".join(json.dumps(lst) for lst in sorted(cycles.values(), key=key))
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

    if new_cycles := pr.keys() - base.keys():
        print("# Circular import analysis")
        print()
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

    if removed_cycles := base.keys() - pr.keys():
        print(
            "The following circular imports among modules have been removed on "
            "this PR, when compared to the base branch:"
        )
        print()
        print_cycles([base[_] for _ in removed_cycles])

    return bool(new_cycles)


def main() -> bool:
    argp = ArgumentParser()

    subp = argp.add_subparsers(dest="command")

    subp_analyze = subp.add_parser("analyze")
    subp_analyze.add_argument("output", type=Path)

    subp_compare = subp.add_parser("compare")
    subp_compare.add_argument("base", type=Path)
    subp_compare.add_argument("pr", type=Path)

    args = argp.parse_args()

    return globals()[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
