# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "betsy @ git+https://github.com/p403n1x87/betsy.git",
# ]
# ///
"""Find ddtrace.internal (or ddtrace.contrib) modules that are really product-specific.

layers.py enforces that ddtrace.internal/ddtrace.contrib don't *import* product
code. This script looks the other way: it inspects who *imports* each
internal-core module, and flags modules that are, in practice, only used by a
single product -- a sign the module was never generic and should live in that
product's own namespace (e.g. ddtrace.internal.<product>) instead of the
shared foundation layer.

This is advisory: unlike layers.py it isn't meant to gate CI, since dominance
is a judgement call (see "Confidence tiers" below), not a hard rule.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import TypedDict

from betsy import DependencyGraph
from betsy import compute_metrics


sys.path.insert(0, str(Path(__file__).parent))
from layers import _violates  # noqa: E402


_Candidate = TypedDict(
    "_Candidate",
    {
        "module": str,
        "dominant_zone": str,
        "confidence": str,
        "dominant_ratio": float,
        "importer_zones": dict[str, int],
        "minority_importers": list[str],
        "total_importers": int,
        "score": int,
        "confirmed_violations": list[str],
    },
)

# Module prefixes we don't control and can't restructure.
_EXCLUDED_PREFIXES = {"ddtrace.vendor"}

_CONFIG_PATH = Path(__file__).with_name("layers.json")

# A module used by exactly one product is "exclusive" evidence. A module used
# by more than one, where one product still accounts for most of the
# importers, is "dominant" -- weaker, worth a human look at the minority
# importers before moving anything.
_DOMINANT_RATIO = 0.6
_MAX_ITERATIONS = 10


def _load_config(config_path: Path) -> tuple[dict[str, str], set[str], set[tuple[str, str]], bool, set[str]]:
    config = json.loads(config_path.read_text())
    zones: dict[str, str] = config["zones"]
    # The zones layers.py already treats as "must serve every product" --
    # reused here as the default set to check for hidden per-product
    # ownership, instead of hardcoding a single zone name.
    forbid_from: set[str] = set(config["rules"]["forbid_from_zones_into_products"])
    exceptions: set[tuple[str, str]] = {(e["from_zone"], e["to_zone"]) for e in config["rules"]["exceptions"]}
    forbid_products_into_contrib: bool = config["rules"]["forbid_products_into_contrib"]["enabled"]
    namespace_buckets: set[str] = set(config["affinity"]["namespace_buckets"])
    return zones, forbid_from, exceptions, forbid_products_into_contrib, namespace_buckets


def _in_namespace_bucket(module: str, namespace_buckets: set[str]) -> bool:
    parent = module.rsplit(".", 1)[0]
    return parent in namespace_buckets


def _zone_of(module: str, zones: dict[str, str], prefixes: list[str]) -> str | None:
    for prefix in prefixes:
        if module == prefix or module.startswith(prefix + "."):
            return zones[prefix]
    return None


def _reverse_graph(graph: DependencyGraph) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {name: set() for name in graph.data}
    for importer, imports in graph.data.items():
        for imported in imports:
            if imported in reverse and imported != importer:
                reverse[imported].add(importer)
    return reverse


def _classify(
    importers: set[str],
    zones: dict[str, str],
    prefixes: list[str],
    candidate_zones: set[str],
    resolved: dict[str, tuple[str, float] | None],
) -> tuple[dict[str, int], int]:
    """Return (product -> importer count, total importers counted towards the ratio).

    An importer casts a vote for a product when it's either a product itself,
    or another module in one of the zones under analysis that has already
    resolved to a product (propagating affinity through zone-internal
    chains). Everything else -- foundation code, or a same-zone importer that
    hasn't itself resolved to a product -- is neutral: it still counts
    towards the denominator (diluting the ratio), it just doesn't vote for
    anyone. Without this, a module imported by one product and by generic
    bootstrap/plugin code (unclassified, so foundation) would read as "100%
    exclusive" to that product -- the foundation caller is exactly the
    evidence that it isn't.
    """
    votes: Counter[str] = Counter()
    total = 0
    for importer in importers:
        zone = _zone_of(importer, zones, prefixes)
        total += 1
        if zone is None:
            continue
        if zone.startswith("product:"):
            votes[zone] += 1
        elif zone in candidate_zones:
            prior = resolved.get(importer)
            if prior is not None:
                votes[prior[0]] += 1
    return dict(votes), total


def analyze(args: argparse.Namespace) -> None:
    root = args.root if args.root is not None else Path(__file__).parents[2] / "ddtrace"
    root = root.resolve()
    graph = DependencyGraph(root=root, include={"ddtrace"}, exclude=_EXCLUDED_PREFIXES)
    zones, forbid_from, exceptions, forbid_products_into_contrib, namespace_buckets = _load_config(_CONFIG_PATH)
    candidate_zones = set(args.zones) if args.zones else forbid_from
    prefixes = sorted(zones, key=len, reverse=True)
    metrics = compute_metrics(graph)
    reverse = _reverse_graph(graph)

    candidate_modules = [m for m in graph.data if _zone_of(m, zones, prefixes) in candidate_zones]

    # Fixed-point iteration: a module's dominant zone can feed into the
    # dominance computation of the modules that import *it*, so resolving one
    # module can change the answer for another. Not strictly monotonic (a
    # newly-arriving vote can shift which product dominates), so we cap
    # iterations rather than require true convergence -- this is an advisory
    # tool, not a correctness-critical analysis.
    resolved: dict[str, tuple[str, float] | None] = dict.fromkeys(candidate_modules)
    for _ in range(_MAX_ITERATIONS):
        changed = False
        for module in candidate_modules:
            votes, total = _classify(reverse.get(module, set()), zones, prefixes, candidate_zones, resolved)
            if not votes:
                new_value = None
            else:
                zone, count = max(votes.items(), key=lambda kv: kv[1])
                ratio = count / total
                new_value = (zone, ratio) if ratio >= _DOMINANT_RATIO else None
            if resolved[module] != new_value:
                resolved[module] = new_value
                changed = True
        if not changed:
            break

    candidates: list[_Candidate] = []
    for module in candidate_modules:
        # A confirmed violation is a hard fact -- layers.py's own rule already
        # flags a direct importer -> module edge (e.g. a product importing
        # straight into contrib) -- independent of whether this module's
        # overall importer mix clears the heuristic dominance threshold
        # below. It must not be gated behind that threshold, or a module
        # diluted by unrelated neutral importers would hide a real violation.
        own_zone = _zone_of(module, zones, prefixes)
        confirmed_violations = sorted(
            importer
            for importer in reverse.get(module, set())
            if own_zone is not None
            and (importer_zone := _zone_of(importer, zones, prefixes)) is not None
            and _violates(importer_zone, own_zone, forbid_from, exceptions, forbid_products_into_contrib)
        )
        votes, total = _classify(reverse.get(module, set()), zones, prefixes, candidate_zones, resolved)
        if not votes:
            continue
        dominant_zone, count = max(votes.items(), key=lambda kv: kv[1])
        ratio = count / total if total else 0.0
        if ratio < _DOMINANT_RATIO and not confirmed_violations:
            continue
        # A namespace bucket (e.g. ddtrace.internal.settings) is intentionally
        # structured with one child module per product; "exclusive to product
        # X" is true but not a finding there, since the module was never
        # meant to be generic. A confirmed violation still counts -- the
        # bucket doesn't excuse an actual layers.py rule violation.
        if not confirmed_violations and _in_namespace_bucket(module, namespace_buckets):
            continue
        confidence = "exclusive" if len(votes) == 1 else "dominant"
        minority = sorted(
            importer
            for importer in reverse.get(module, set())
            if (importer_zone := _zone_of(importer, zones, prefixes)) is not None
            and importer_zone.startswith("product:")
            and importer_zone != dominant_zone
        )
        target = metrics.get(module)
        candidates.append(
            {
                "module": module,
                "dominant_zone": dominant_zone,
                "confidence": confidence,
                "dominant_ratio": round(ratio, 3),
                "importer_zones": votes,
                "minority_importers": minority,
                "total_importers": total,
                "score": target.ca if target is not None else 0,
                "confirmed_violations": confirmed_violations,
            }
        )

    candidates.sort(
        key=lambda c: (not c["confirmed_violations"], c["confidence"] != "exclusive", -c["score"], c["module"])
    )

    args.output.write_text(json.dumps({"candidates": candidates}, indent=2) + "\n")

    confirmed = [c for c in candidates if c["confirmed_violations"]]
    exclusive = [c for c in candidates if not c["confirmed_violations"] and c["confidence"] == "exclusive"]
    dominant = [c for c in candidates if not c["confirmed_violations"] and c["confidence"] == "dominant"]
    print(f"Found {len(confirmed)} module(s) with a direct import already flagged as a layers.py violation.")
    print(f"Found {len(exclusive)} module(s) used exclusively by a single product.")
    print(f"Found {len(dominant)} module(s) mostly used by a single product (review minority importers).")


def _print_candidate(c: _Candidate) -> None:
    zones_str = ", ".join(f"{z}={n}" for z, n in sorted(c["importer_zones"].items(), key=lambda kv: -kv[1]))
    print(f"  {c['module']}  (score={c['score']}, ratio={c['dominant_ratio']})")
    print(f"    -> {c['dominant_zone']}  [{zones_str}]")
    if c["confirmed_violations"]:
        print(f"    confirmed layers.py violation, imported directly by: {', '.join(c['confirmed_violations'])}")
    if c["minority_importers"]:
        print(f"    minority importers to review: {', '.join(c['minority_importers'])}")


def report(args: argparse.Namespace) -> None:
    data = json.loads(args.candidates.read_text())
    candidates: list[_Candidate] = data["candidates"]
    confirmed = [c for c in candidates if c["confirmed_violations"]]
    exclusive = [c for c in candidates if not c["confirmed_violations"] and c["confidence"] == "exclusive"]
    dominant = [c for c in candidates if not c["confirmed_violations"] and c["confidence"] == "dominant"]

    if confirmed:
        print(f"## Confirmed layering violations, also caught by layers.py ({len(confirmed)})")
        print()
        print(
            "These aren't just heuristically dominated by one product -- at least one direct importer "
            "already violates layers.py's rules (e.g. a product importing straight into contrib). "
            "Fix these the way layers.py violations are fixed; see the dependency-direction-analysis skill."
        )
        print()
        for c in confirmed:
            _print_candidate(c)
        print()

    if exclusive:
        print(f"## Exclusive to one product ({len(exclusive)})")
        print()
        for c in exclusive:
            _print_candidate(c)
        print()

    if dominant:
        print(f"## Dominated by one product, minority use elsewhere ({len(dominant)})")
        print()
        for c in dominant:
            _print_candidate(c)


def main() -> int:
    argp = argparse.ArgumentParser()
    subp = argp.add_subparsers(dest="command", required=True)

    subp_analyze = subp.add_parser("analyze", help="Compute per-product affinity of internal-core/contrib modules")
    subp_analyze.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Path to the ddtrace package root (default: auto-detected from __file__)",
    )
    subp_analyze.add_argument(
        "--zones",
        type=lambda s: [z.strip() for z in s.split(",")],
        default=None,
        help=(
            "Comma-separated zones to check for hidden per-product ownership "
            "(default: layers.json's rules.forbid_from_zones_into_products, "
            "i.e. every zone that's supposed to serve every product)"
        ),
    )
    subp_analyze.add_argument("output", type=Path)

    subp_report = subp.add_parser("report", help="Pretty-print an affinity.py analyze output")
    subp_report.add_argument("candidates", type=Path)

    args = argp.parse_args()

    globals()[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
