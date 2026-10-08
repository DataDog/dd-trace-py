# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "betsy @ git+https://github.com/p403n1x87/betsy.git",
# ]
# ///
import argparse
import json
from pathlib import Path
import sys

from betsy import DependencyGraph
from betsy.metrics import _strongly_connected_components


_Cycle = tuple[str, ...]
_CycleMap = dict[frozenset[str], _Cycle]

# Module prefixes we don't control and can't restructure to break cycles (e.g.
# third-party code we vendor verbatim), so there's no point reporting them.
_EXCLUDED_PREFIXES = {"ddtrace.vendor"}


def _build_graph(root: Path) -> dict[str, set[str]]:
    g: dict[str, set[str]] = DependencyGraph(root=root.resolve(), include={"ddtrace"}, exclude=_EXCLUDED_PREFIXES).data
    for importer, imports in list(g.items()):
        if not imports:
            del g[importer]
    return g


def _find_cycle(start: str, g: dict[str, set[str]]) -> _Cycle | None:
    """DFS a graph restricted to one strongly connected component for a back edge.

    `visited` is shared across the whole search (not copied per branch), so a
    node is explored at most once: a dead-end subtree is never retried from a
    different parent. That keeps this O(V+E) instead of enumerating every
    simple cycle in the component, which is exponential on a dense SCC.

    Sorted neighbor order makes the result deterministic across processes,
    which matters because compare() diffs cycles between separate CI runs.
    """
    visited: set[str] = set()
    stack: list[str] = []

    def visit(v: str) -> _Cycle | None:
        visited.add(v)
        stack.append(v)
        for i in sorted(g.get(v, set())):
            if i not in visited:
                found = visit(i)
                if found is not None:
                    return found
            elif i in stack:
                return tuple(stack[stack.index(i) :] + [i])
        stack.pop()
        return None

    return visit(start)


def _representative_cycle(component: set[str], g: dict[str, set[str]]) -> _Cycle:
    """Pick one simple cycle out of a strongly connected component.

    Restricting the search graph to the component's own nodes guarantees a
    back edge is found almost immediately, since every node in the component
    is mutually reachable from every other by definition.
    """
    # Exclude self-edges here too, for the same reason `analyze()` excludes them
    # from `adj`: a module importing itself isn't a cycle between distinct
    # modules, and leaving it in lets the DFS "discover" a degenerate
    # single-node cycle before ever reaching a real one.
    subgraph = {v: (g.get(v, set()) & component) - {v} for v in component}
    cycle = _find_cycle(min(component), subgraph)
    assert cycle is not None, "a strongly connected component of size > 1 always contains a cycle"  # nosec
    return cycle


def analyze(args: argparse.Namespace) -> None:
    root = args.root if args.root is not None else Path(__file__).parents[2] / "ddtrace"
    g = _build_graph(root)

    nodes: set[str] = set(g)
    for imports in g.values():
        nodes |= imports
    # Self-imports (a package importing its own submodule) aren't circular
    # imports between distinct modules, so exclude self-edges the same way
    # betsy's own NCCD computation does.
    adj = {v: g.get(v, set()) - {v} for v in nodes}

    # Keyed by the *whole* component, not by the representative cycle's own
    # nodes: if a PR adds a new module to an already-cyclic component (e.g.
    # base has A<->B, PR adds A<->C), the representative cycle can still be
    # A->B->A, so keying on the printed cycle would hide C joining the tangle
    # from compare().
    cycles: _CycleMap = {
        frozenset(component): _representative_cycle(component, g)
        for component in _strongly_connected_components(nodes, adj)
        if len(component) > 1
    }

    entries = sorted(
        ({"nodes": sorted(members), "cycle": list(cycle)} for members, cycle in cycles.items()),
        key=lambda entry: len(entry["cycle"]),
    )
    res = ",\n".join(json.dumps(entry) for entry in entries)
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
        return {frozenset(entry["nodes"]): tuple(entry["cycle"]) for entry in json.loads(path.read_text())}

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
