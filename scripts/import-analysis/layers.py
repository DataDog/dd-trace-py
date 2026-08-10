# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "betsy @ git+https://github.com/p403n1x87/betsy.git",
# ]
# ///
import argparse
import json
from pathlib import Path
import sys
from typing import TypedDict

from betsy import DependencyGraph
from betsy import ModuleMetrics
from betsy import compute_metrics


_Violation = TypedDict(
    "_Violation",
    {
        "from": str,
        "to": str,
        "from_zone": str,
        "to_zone": str,
        "score": int,
        "in_tangle": bool,
    },
)

# Module prefixes we don't control and can't restructure (e.g. vendored
# third-party code), so there's no point reporting them.
_EXCLUDED_PREFIXES = {"ddtrace.vendor"}

_CONFIG_PATH = Path(__file__).with_name("layers.json")

# Base severity per rule kind, before per-edge structural adjustments. A
# layer that's supposed to be foundational (internal-core, contrib) reaching
# up into a product is architecturally worse than two products leaking into
# each other, so it starts with a higher weight.
_RULE_WEIGHTS = {
    "internal-core": 3,
    "contrib": 2,
}
_PRODUCT_PRODUCT_WEIGHT = 1

# Extra severity added when the imported module is itself part of an import
# tangle (nccd > 1.0, i.e. its strongly connected component is bigger than a
# single module) -- the layering violation is compounding an existing
# circular-import problem, not just crossing a boundary once.
_CYCLE_BONUS = 5


def _load_zones(config_path: Path) -> tuple[dict[str, str], set[str], set[tuple[str, str]], set[str]]:
    config = json.loads(config_path.read_text())
    zones: dict[str, str] = config["zones"]
    forbid_from: set[str] = set(config["rules"]["forbid_from_zones_into_products"])
    exceptions: set[tuple[str, str]] = {(e["from_zone"], e["to_zone"]) for e in config["rules"]["exceptions"]}
    foundation_top_level: set[str] = set(config["foundation"]["top_level"])
    return zones, forbid_from, exceptions, foundation_top_level


def _uncovered_top_level(root: Path, zones: dict[str, str], foundation_top_level: set[str]) -> list[str]:
    """Direct children of the ddtrace package root with no zone and no foundation exemption.

    Only scans one level deep: a brand new ``ddtrace/<x>`` package or module is a
    conscious architectural addition that should be classified one way or another,
    whereas deeper additions (e.g. a new file inside an already-classified package)
    inherit their parent's zone via prefix matching and don't need a separate check.
    """
    uncovered = []
    for child in sorted(root.iterdir()):
        if child.name in {"__pycache__", "__init__.py", "py.typed"}:
            continue
        is_package = child.is_dir() and (child / "__init__.py").is_file()
        is_module = child.is_file() and child.suffix == ".py"
        if not (is_package or is_module):
            continue
        name = f"{root.name}.{child.stem}"
        if name not in zones and name not in foundation_top_level:
            uncovered.append(name)
    return uncovered


def _zone_of(module: str, zones: dict[str, str], prefixes: list[str]) -> str | None:
    for prefix in prefixes:
        if module == prefix or module.startswith(prefix + "."):
            return zones[prefix]
    return None


def _violates(from_zone: str, to_zone: str, forbid_from: set[str], exceptions: set[tuple[str, str]]) -> bool:
    if from_zone == to_zone or (from_zone, to_zone) in exceptions:
        return False
    is_product = to_zone.startswith("product:")
    if from_zone in forbid_from:
        return is_product
    return from_zone.startswith("product:") and is_product


def _severity(from_zone: str, metrics: dict[str, ModuleMetrics], imported: str) -> int:
    weight = _RULE_WEIGHTS.get(from_zone, _PRODUCT_PRODUCT_WEIGHT)
    target = metrics.get(imported)
    ca = target.ca if target is not None else 0
    in_tangle = target is not None and target.nccd > 1.0
    return weight + ca + (_CYCLE_BONUS if in_tangle else 0)


def analyze(args: argparse.Namespace) -> None:
    root = args.root if args.root is not None else Path(__file__).parents[2] / "ddtrace"
    root = root.resolve()
    graph = DependencyGraph(root=root, include={"ddtrace"}, exclude=_EXCLUDED_PREFIXES)
    zones, forbid_from, exceptions, foundation_top_level = _load_zones(_CONFIG_PATH)
    prefixes = sorted(zones, key=len, reverse=True)
    metrics = compute_metrics(graph)

    violations: list[_Violation] = []
    for importer, imports in graph.data.items():
        from_zone = _zone_of(importer, zones, prefixes)
        if from_zone is None:
            continue
        for imported in imports:
            to_zone = _zone_of(imported, zones, prefixes)
            if to_zone is None or not _violates(from_zone, to_zone, forbid_from, exceptions):
                continue
            target = metrics.get(imported)
            violations.append(
                {
                    "from": importer,
                    "to": imported,
                    "from_zone": from_zone,
                    "to_zone": to_zone,
                    "score": _severity(from_zone, metrics, imported),
                    "in_tangle": target is not None and target.nccd > 1.0,
                }
            )

    violations.sort(key=lambda v: (-v["score"], v["from"], v["to"]))
    uncovered = _uncovered_top_level(root, zones, foundation_top_level)

    args.output.write_text(json.dumps({"violations": violations, "uncovered": uncovered}, indent=2) + "\n")

    if violations:
        print(f"Detected {len(violations)} dependency direction violations.")
    if uncovered:
        print(f"Detected {len(uncovered)} top-level module(s) not covered by layers.json: {', '.join(uncovered)}")


_PREVIEW = 5  # max violations shown inline per section before collapsing into <details>

_ARTIFACTS_HINT = (
    "> To see all violations, download the `layers-base.json` and `layers-pr.json` artifacts "
    "from this CI job and run:\n"
    "> ```\n"
    "> uv run --script scripts/import-analysis/layers.py compare layers-base.json layers-pr.json\n"
    "> ```"
)


def _key(v: _Violation) -> tuple[str, str]:
    return (v["from"], v["to"])


def compare(args: argparse.Namespace) -> bool:
    def load(path: Path) -> tuple[dict[tuple[str, str], _Violation], set[str]]:
        data = json.loads(path.read_text())
        return {_key(v): v for v in data["violations"]}, set(data["uncovered"])

    (base, base_uncovered), (pr, pr_uncovered) = map(load, [args.base, args.pr])

    def describe(v: _Violation, delta: int | None = None) -> str:
        arrow = "=×=>" if v["in_tangle"] else "-×->"
        trend = f", {delta:+d} vs base" if delta else ""
        return f"{v['from']} {arrow} {v['to']}  ({v['from_zone']} -> {v['to_zone']}, score={v['score']}{trend})"

    def print_lines(lines: list[str]) -> None:
        print("```")
        for line in lines:
            print(line)
        print("```")
        print()

    def print_capped(lines: list[str], summary: str) -> None:
        """Print up to _PREVIEW lines inline; collapse the rest into a <details> block."""
        if len(lines) <= _PREVIEW:
            print_lines(lines)
        else:
            print(f"<details><summary>{summary} (showing {_PREVIEW} of {len(lines)} highest severity)</summary>")
            print()
            print_lines(lines[:_PREVIEW])
            print("</details>")
            print()
            print(_ARTIFACTS_HINT)
        print()

    new_keys = pr.keys() - base.keys()
    removed_keys = base.keys() - pr.keys()
    existing_keys = base.keys() & pr.keys()
    worsened_keys = {k for k in existing_keys if pr[k]["score"] > base[k]["score"]}

    if new_keys:
        sorted_new = sorted((pr[k] for k in new_keys), key=lambda v: -v["score"])
        print("## 🚨 New dependency direction violations detected 🚨")
        print()
        print(
            f"**{len(new_keys)}** new violation(s) of the dependency direction rules have been introduced by this PR:"
        )
        print()
        print_capped([describe(v) for v in sorted_new], "Show new violations")
        print(
            "`ddtrace.internal` and `ddtrace.contrib` must not depend on product code, and products must not "
            "depend on each other directly. See the `dependency-direction-analysis` skill for how to fix this."
        )
        print()

    if worsened_keys:
        sorted_worsened = sorted(worsened_keys, key=lambda k: -(pr[k]["score"] - base[k]["score"]))
        print("## 📈 Existing violations got worse")
        print()
        print(
            f"**{len(worsened_keys)}** pre-existing violation(s) increased in severity (e.g. their target became "
            "more depended-on, or got pulled into an import cycle), though the edge itself isn't new:"
        )
        print()
        print_capped(
            [describe(pr[k], delta=pr[k]["score"] - base[k]["score"]) for k in sorted_worsened],
            "Show violations that got worse",
        )

    if existing_keys:
        sorted_existing = sorted((pr[k] for k in existing_keys), key=lambda v: -v["score"])
        print("## ⚠️ Existing dependency direction violations")
        print()
        print(
            f"There are **{len(existing_keys)}** dependency direction violations that already exist on the base "
            "branch and have not been changed by this PR."
        )
        print()
        print_capped([describe(v) for v in sorted_existing], "Show existing violations")

    if removed_keys:
        sorted_removed = sorted((base[k] for k in removed_keys), key=lambda v: -v["score"])
        print("## ✅ Dependency direction violations removed")
        print()
        print(f"**{len(removed_keys)}** violation(s) have been removed by this PR.")
        print()
        print_capped([describe(v) for v in sorted_removed], "Show removed violations")

    new_uncovered = pr_uncovered - base_uncovered
    existing_uncovered = base_uncovered & pr_uncovered
    removed_uncovered = base_uncovered - pr_uncovered

    if new_uncovered:
        print("## 🚨 New top-level modules missing from layers.json 🚨")
        print()
        print(
            f"**{len(new_uncovered)}** new top-level `ddtrace` package/module was added without being classified "
            "in `scripts/import-analysis/layers.json` (as a zone, or explicitly as foundation code):"
        )
        print()
        print_lines(sorted(new_uncovered))
        print(
            "Add it to `zones` (choose the right product/contrib/internal-core zone) or to "
            "`foundation.top_level` if it's deliberately exempt from dependency direction rules. "
            "See the `dependency-direction-analysis` skill."
        )
        print()

    if existing_uncovered:
        print("## ⚠️ Pre-existing uncategorized top-level modules")
        print()
        print(
            f"**{len(existing_uncovered)}** top-level module(s) are still not classified in `layers.json`, "
            "unchanged by this PR:"
        )
        print()
        print_lines(sorted(existing_uncovered))

    if removed_uncovered:
        print("## ✅ Top-level modules now classified")
        print()
        print(f"**{len(removed_uncovered)}** top-level module(s) were classified in `layers.json` by this PR:")
        print()
        print_lines(sorted(removed_uncovered))

    return bool(new_keys) or bool(new_uncovered)


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
