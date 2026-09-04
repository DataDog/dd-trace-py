#!/usr/bin/env python3
"""Generate a human-readable report from a GCMonitor ``gc-stats.json`` snapshot.

The file is produced by the native GC monitor (see
``ddtrace/internal/datadog/profiling/dd_wrapper/src/gc_monitor.cpp``). It is a
single JSON object describing one snapshot:

    {
      "v": 1,
      "ts_ns": <snapshot time, ns since epoch>,
      "gc": {
        "enabled": bool,
        "thresholds": [g0, g1, g2],
        "garbage": <len(gc.garbage)>,
        "gen":   [{"n","col","uncol"} x3],   # cumulative gc.get_stats()
        "d_gen": [{"n","col","uncol"} x3]     # delta since previous snapshot
      },
      "tt": ["type.name", ...],               # type table
      "tc": [<instance count>, ...],          # per-type instance count (parallel to tt)
      "r":  [ <root>, ... ],                  # suspected long-lived objects
      "rt": [ <node>, ... ]                   # type -> type reference tree (see below)
    }

A "suspect" is an object that survived several consecutive snapshots, i.e. a
potential leak. Suspects are grouped into "roots" by where they are anchored:

    c == 'K'  Stack   -- held alive by a live frame      (fn = "func (file:line)")
    c == 'S'  Static  -- held alive by a module global   (fn = "module.attr")
    c == 'O'  Other   -- referrer is outside the GC heap (C/extension owned)
    c == '?'  Unknown -- referrer walking disabled / inconclusive

Each root carries ``ic`` (instance count) and ``ts`` (total shallow bytes).

The "rt" reference tree is built from ``gc.get_referents`` (only present when
DD_PROFILING_GC_REFERRERS=true). Each node is::

    {"t": <type idx>, "ic": <count>, "ts": <bytes>, "ch": [ <node>, ... ]}

A node's children are the types that instances of the node's type *effectively
retain* (the "holder type -> held type" direction). Generic containers
(``dict``/``list``/``set``/``tuple``, including the instance ``__dict__``) are
traversed transparently on the real object graph, so an attribute held in a
container is attributed to the owning object -- e.g. ``BundleIndex -> Bundle``
rather than ``BundleIndex -> dict``. For a *root* node, ``ic``/``ts`` are the
type's live instance count and total shallow size; for a *child* node, ``ic`` is
the number of held instances reached from the parent type and ``ts`` is the
shallow bytes they retain. Following a chain ``A -> B -> C`` reads as "instances
of A retain instances of B, which retain instances of C".
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from typing import Any
from typing import Optional
from typing import Sequence

import gc_stats_graph


CATEGORY_NAMES: dict[str, str] = {
    "K": "Stack (live frame)",
    "S": "Static (module global)",
    "O": "Other (outside GC heap / C-owned)",
    "?": "Unknown (referrers unavailable)",
}

GEN_NAMES: tuple[str, str, str] = ("gen0 (young)", "gen1", "gen2 (old)")

DEFAULT_THRESHOLDS: tuple[int, int, int] = (700, 10, 10)

# Sizes above this are physically impossible for a single process and indicate
# a bad shallow-size computation in the producer (e.g. reading ob_size for a
# type whose ob_size is not an item count, such as ``frame``). We render these
# distinctly instead of printing a nonsensical "PiB" figure.
IMPLAUSIBLE_BYTES: int = 1 << 48  # 256 TiB


def human_bytes(n: int) -> str:
    """Render a byte count using binary units."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{n} B"


def fmt_bytes(n: int) -> str:
    """Like ``human_bytes`` but flags implausibly large (overflowed) values."""
    if n >= IMPLAUSIBLE_BYTES:
        return f"<overflow:{n}>"
    return human_bytes(n)


def fmt_ts(ts_ns: int) -> str:
    """Render a ns-since-epoch timestamp as a readable UTC string."""
    if ts_ns <= 0:
        return "<unknown>"
    seconds = ts_ns / 1e9
    dt = _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def load_snapshot(path: str) -> dict[str, Any]:
    """Read and validate a gc-stats.json file."""
    with open(path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    if not isinstance(data, dict) or "gc" not in data:
        raise ValueError(f"{path} does not look like a gc-stats snapshot")
    return data


class Report:
    """Builds a textual report and accumulates findings to look into."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.tt: list[str] = data.get("tt", [])
        self.tc: list[int] = data.get("tc", [])
        self.roots: list[dict[str, Any]] = data.get("r", [])
        self.rt: list[dict[str, Any]] = data.get("rt", [])
        self.gc: dict[str, Any] = data.get("gc", {})
        self.lines: list[str] = []
        self.findings: list[str] = []

    def out(self, line: str = "") -> None:
        self.lines.append(line)

    def finding(self, text: str) -> None:
        self.findings.append(text)

    def type_name(self, idx: int) -> str:
        if 0 <= idx < len(self.tt):
            return self.tt[idx]
        return f"<type#{idx}>"

    # -- sections ---------------------------------------------------------

    def header(self) -> None:
        self.out("=" * 78)
        self.out("GC MONITOR SNAPSHOT REPORT")
        self.out("=" * 78)
        self.out(f"format version : {self.data.get('v', '?')}")
        self.out(f"snapshot time  : {fmt_ts(int(self.data.get('ts_ns', 0)))}")
        self.out(f"types tracked  : {len(self.tt)}")
        self.out(f"suspect roots  : {len(self.roots)}")
        if self.rt:
            self.out(f"ref-tree roots : {len(self.rt)} ({_count_nodes(self.rt)} nodes)")
        self.out("")

    def gc_engine(self) -> None:
        self.out("-" * 78)
        self.out("GC ENGINE STATE")
        self.out("-" * 78)

        enabled = bool(self.gc.get("enabled", True))
        thresholds = tuple(self.gc.get("thresholds", []))
        garbage = int(self.gc.get("garbage", 0))

        self.out(f"gc.isenabled()   : {enabled}")
        self.out(f"gc.get_threshold : {thresholds}")
        self.out(f"len(gc.garbage)  : {garbage}")
        self.out("")

        if not enabled:
            self.finding(
                "GC is DISABLED. Cyclic garbage will never be reclaimed automatically; "
                "memory growth is expected until gc.collect() is called manually."
            )
        if tuple(thresholds) and tuple(thresholds) != DEFAULT_THRESHOLDS:
            self.finding(
                f"GC thresholds {thresholds} differ from CPython defaults {DEFAULT_THRESHOLDS}; "
                "collection cadence has been tuned and may be too lax."
            )
        if garbage > 0:
            self.finding(
                f"gc.garbage holds {garbage} uncollectable object(s). These are cycles the "
                "collector found but could not free (e.g. legacy __del__ finalizers). Inspect gc.garbage."
            )

        gen = self.gc.get("gen", [])
        d_gen = self.gc.get("d_gen", [])
        self.out(f"{'generation':<14}{'collections':>14}{'collected':>14}{'uncollectable':>16}")
        for i in range(min(3, len(gen))):
            g = gen[i]
            self.out(
                f"{GEN_NAMES[i]:<14}{int(g.get('n', 0)):>14}{int(g.get('col', 0)):>14}{int(g.get('uncol', 0)):>16}"
            )
        self.out("")

        if d_gen:
            self.out("Delta since previous snapshot:")
            self.out(f"{'generation':<14}{'collections':>14}{'collected':>14}{'uncollectable':>16}")
            for i in range(min(3, len(d_gen))):
                g = d_gen[i]
                self.out(
                    f"{GEN_NAMES[i]:<14}{int(g.get('n', 0)):>14}{int(g.get('col', 0)):>14}{int(g.get('uncol', 0)):>16}"
                )
            self.out("")

        total_uncol = sum(int(g.get("uncol", 0)) for g in gen)
        if total_uncol > 0:
            self.finding(
                f"{total_uncol} uncollectable object(s) accumulated across generations "
                "(gc.get_stats 'uncollectable'). Indicates cycles the GC cannot break."
            )

        # A gen2 (full) collection that frees little while gen0 churns a lot is a
        # classic sign of a growing old generation.
        if len(d_gen) >= 3:
            g2_runs = int(d_gen[2].get("n", 0))
            g2_freed = int(d_gen[2].get("col", 0))
            if g2_runs > 0 and g2_freed == 0:
                self.finding(
                    "Full (gen2) collections ran but freed nothing in this interval; "
                    "old-generation objects are surviving -- likely a real leak."
                )

    def suspects(self, top: int) -> None:
        self.out("-" * 78)
        self.out("SUSPECTED LONG-LIVED OBJECTS (potential leaks)")
        self.out("-" * 78)

        if not self.roots:
            self.out("No suspects recorded. Either nothing survived the survivor threshold,")
            self.out("or referrer tracking was disabled and no objects qualified.")
            self.out("")
            return

        total_bytes = sum(int(r.get("ts", 0)) for r in self.roots)
        total_count = sum(int(r.get("ic", 0)) for r in self.roots)
        self.out(f"total suspects : {total_count} object(s), {human_bytes(total_bytes)}")
        self.out("")

        # By category.
        by_cat: dict[str, list[int]] = {}
        for r in self.roots:
            cat = str(r.get("c", "?"))
            agg = by_cat.setdefault(cat, [0, 0])
            agg[0] += int(r.get("ic", 0))
            agg[1] += int(r.get("ts", 0))
        self.out("By anchor category:")
        for cat, (cnt, size) in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
            name = CATEGORY_NAMES.get(cat, f"category '{cat}'")
            self.out(f"  {name:<40}{cnt:>8} obj  {human_bytes(size):>12}")
        self.out("")

        # Ranked roots by total bytes.
        ranked = sorted(self.roots, key=lambda r: int(r.get("ts", 0)), reverse=True)
        shown = ranked[:top]
        self.out(f"Top {len(shown)} roots by retained shallow size:")
        self.out(f"  {'#':>2}  {'cat':<4}{'count':>7}{'size':>13}  type / location")
        for i, r in enumerate(shown, 1):
            cat = str(r.get("c", "?"))
            cnt = int(r.get("ic", 0))
            size = int(r.get("ts", 0))
            tname = self.type_name(int(r.get("t", -1)))
            loc = r.get("fn")
            desc = tname if not loc else f"{tname}  <- {loc}"
            self.out(f"  {i:>2}  {cat:<4}{cnt:>7}{human_bytes(size):>13}  {desc}")
        self.out("")

        # Findings from the suspect set.
        static_roots = [r for r in self.roots if r.get("c") == "S"]
        if static_roots:
            top_static = max(static_roots, key=lambda r: int(r.get("ts", 0)))
            self.finding(
                "Module-level (Static) suspects are present -- these are anchored by a global and "
                "tend to be true leaks. Largest: "
                f"{self.type_name(int(top_static.get('t', -1)))} ({top_static.get('fn', '?')}), "
                f"{human_bytes(int(top_static.get('ts', 0)))}."
            )

        biggest = ranked[0]
        self.finding(
            "Largest single suspect root: "
            f"{self.type_name(int(biggest.get('t', -1)))} "
            f"[{CATEGORY_NAMES.get(str(biggest.get('c', '?')), biggest.get('c'))}], "
            f"{int(biggest.get('ic', 0))} object(s), {human_bytes(int(biggest.get('ts', 0)))}."
        )

        all_other = all(r.get("c") == "O" for r in self.roots)
        if all_other and any(r.get("c") == "O" for r in self.roots):
            self.finding(
                "All suspects are category 'Other' (anchored outside the GC heap / by C "
                "extensions). Referrer walking may have been disabled, or these are container "
                "builtins held by native code -- enable referrers for sharper attribution."
            )

    def what_to_look_into(self) -> None:
        self.out("=" * 78)
        self.out("MAIN THINGS TO LOOK INTO")
        self.out("=" * 78)
        if not self.findings:
            self.out("Nothing notable. GC engine looks healthy and no suspects were flagged.")
            self.out("")
            return
        for i, f in enumerate(self.findings, 1):
            wrapped = _wrap(f, width=72, indent="     ")
            self.out(f"  {i:>2}. {wrapped}")
        self.out("")

    def type_counts(self, top: int, filter_str: str = "") -> None:
        self.out("-" * 78)
        self.out("INSTANCE COUNTS BY TYPE")
        if filter_str:
            self.out(f"(filter: {filter_str!r})")
        self.out("-" * 78)

        if not self.tc:
            self.out("No 'tc' field in snapshot (requires a newer build).")
            self.out("")
            return

        pairs = [(self.tt[i], self.tc[i]) for i in range(min(len(self.tt), len(self.tc)))]
        if filter_str:
            pairs = [(t, c) for t, c in pairs if filter_str in t]
        pairs.sort(key=lambda x: x[1], reverse=True)

        total_objects = sum(c for _, c in pairs)
        self.out(f"matched types : {len(pairs)}")
        self.out(f"matched objs  : {total_objects}")
        self.out("")
        self.out(f"  {'#':>4}  {'count':>8}  type")
        for i, (tname, count) in enumerate(pairs[:top], 1):
            self.out(f"  {i:>4}  {count:>8}  {tname}")
        if len(pairs) > top:
            self.out(f"  ... and {len(pairs) - top} more types (use --top-types to show more)")
        self.out("")

    def reference_tree(self, max_depth: int, top_children: int, filters: Sequence[str] = ()) -> None:
        self.out("-" * 78)
        self.out("TYPE REFERENCE TREE (holder type -> held type, via gc.get_referents)")
        self.out("-" * 78)

        if not self.rt:
            self.out("No 'rt' reference tree in snapshot.")
            self.out("Enable it with DD_PROFILING_GC_REFERRERS=true (expensive on large heaps).")
            self.out("")
            return

        patterns = [f for f in filters if f]
        self.out("A child is a type that instances of its parent effectively retain")
        self.out("(generic containers like dict/list/set/tuple are traversed transparently).")
        self.out("  root : <type>  instances=<n>  size=<shallow bytes>")
        self.out("  child: <type>  held=<n>  retained=<shallow bytes of held instances>")
        self.out(f"(max depth {max_depth}, top {top_children} children per node by retained bytes)")
        if patterns:
            self.out(f"(showing only roots whose type matches any of {patterns!r})")
        self.out("")

        roots = self.rt
        if patterns:
            roots = [r for r in roots if any(p in self.type_name(int(r.get("t", -1))) for p in patterns)]
        roots = sorted(roots, key=lambda r: int(r.get("ts", 0)), reverse=True)

        if not roots:
            self.out("(no roots matched)")
            self.out("")
            return

        for root in roots:
            self._render_ref_node(root, depth=0, max_depth=max_depth, top_children=top_children, is_root=True)
        self.out("")

        self._ref_tree_findings()

    def _render_ref_node(
        self, node: dict[str, Any], depth: int, max_depth: int, top_children: int, is_root: bool
    ) -> None:
        indent = "  " * depth
        tname = self.type_name(int(node.get("t", -1)))
        ic = int(node.get("ic", 0))
        ts = int(node.get("ts", 0))
        if is_root:
            self.out(f"{indent}{tname}  instances={ic}  size={fmt_bytes(ts)}")
        else:
            self.out(f"{indent}- {tname}  held={ic}  retained={fmt_bytes(ts)}")

        children: list[dict[str, Any]] = node.get("ch") or []
        if depth >= max_depth or not children:
            return
        children = sorted(children, key=lambda c: int(c.get("ts", 0)), reverse=True)
        for child in children[:top_children]:
            self._render_ref_node(child, depth + 1, max_depth, top_children, is_root=False)
        if len(children) > top_children:
            self.out(f"{'  ' * (depth + 1)}... and {len(children) - top_children} more child type(s)")

    def _ref_tree_findings(self) -> None:
        # The producer aggregates each (holder, held) edge globally, so every
        # occurrence of an edge in the unrolled tree carries the same retained
        # byte count. Collapse them with max() and surface the heaviest. Skip
        # implausible (overflowed) sizes so they don't dominate the ranking.
        edges: dict[tuple[str, str], int] = {}
        overflow = 0

        def walk(node: dict[str, Any]) -> None:
            nonlocal overflow
            pname = self.type_name(int(node.get("t", -1)))
            if int(node.get("ts", 0)) >= IMPLAUSIBLE_BYTES:
                overflow += 1
            for child in node.get("ch") or []:
                cts = int(child.get("ts", 0))
                if cts < IMPLAUSIBLE_BYTES:
                    key = (pname, self.type_name(int(child.get("t", -1))))
                    if cts > edges.get(key, 0):
                        edges[key] = cts
                walk(child)

        for root in self.rt:
            walk(root)

        if overflow:
            self.finding(
                f"{overflow} reference-tree node(s) report an implausibly large size "
                f"(>= {human_bytes(IMPLAUSIBLE_BYTES)}); the producer's shallow-size computation "
                "overflowed for some type (e.g. 'frame', whose ob_size is not an item count). "
                "Treat those sizes as bogus."
            )

        if edges:
            top = sorted(edges.items(), key=lambda kv: kv[1], reverse=True)[:10]
            self.out("Heaviest reference relationships (holder -> held, by retained shallow bytes):")
            for (parent, child), ts in top:
                self.out(f"  {fmt_bytes(ts):>14}  {parent} -> {child}")
            self.out("")
            (hp, hc), hts = top[0]
            self.finding(
                f"Largest reference relationship: instances of {hp} retain {human_bytes(hts)} of {hc}. "
                f"Check whether {hp} is holding {hc} alive longer than expected."
            )

    def write_histogram_png(self, path: str, top: int, filter_str: str = "") -> int:
        """Render the per-type instance counts as a horizontal bar chart.

        Returns the number of types plotted. Raises if matplotlib is missing
        or the snapshot has no ``tc`` field.
        """
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("matplotlib is required for --histogram-png (pip install matplotlib)") from exc

        if not self.tc:
            raise RuntimeError("snapshot has no 'tc' field; cannot build a class histogram")

        pairs = [(self.tt[i], self.tc[i]) for i in range(min(len(self.tt), len(self.tc)))]
        if filter_str:
            pairs = [(t, c) for t, c in pairs if filter_str in t]
        pairs.sort(key=lambda x: x[1], reverse=True)
        pairs = pairs[:top]
        if not pairs:
            raise RuntimeError("no types matched; nothing to plot")

        names = [t for t, _ in pairs][::-1]
        counts = [c for _, c in pairs][::-1]

        fig_height = max(3.0, 0.28 * len(pairs) + 1.5)
        fig, ax = plt.subplots(figsize=(12, fig_height))
        positions = range(len(names))
        ax.barh(list(positions), counts, color="#774aa4")
        ax.set_yticks(list(positions))
        ax.set_yticklabels(names, fontsize=8)
        ax.set_xlabel("live instance count")
        title = f"GC class histogram (top {len(pairs)} types)"
        if filter_str:
            title += f"  filter={filter_str!r}"
        ax.set_title(title)
        for pos, count in zip(positions, counts):
            ax.text(count, pos, f" {count}", va="center", fontsize=7)
        ax.margins(x=0.08)
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        return len(pairs)

    def _children_dict(self, node: dict[str, Any]) -> dict[str, Any]:
        """Map each child type name to its own nested children dict."""
        return {self.type_name(int(child.get("t", -1))): self._children_dict(child) for child in node.get("ch") or []}

    def tree_json(self) -> dict[str, Any]:
        """The reference-tree forest as nested ``{type: {type: {...}}}`` dicts."""
        return {self.type_name(int(root.get("t", -1))): self._children_dict(root) for root in self.rt}

    def write_tree_json(self, path: str) -> None:
        """Write the simplified reference tree to ``path`` as JSON."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.tree_json(), fh, indent=2)

    def build(
        self,
        top: int,
        show_type_counts: bool = False,
        type_filter: str = "",
        top_types: int = 50,
        show_ref_tree: bool = True,
        ref_depth: int = 4,
        ref_children: int = 8,
        ref_filter: Sequence[str] = (),
    ) -> str:
        self.header()
        self.gc_engine()
        self.suspects(top)
        if show_type_counts:
            self.type_counts(top_types, type_filter)
        if show_ref_tree:
            self.reference_tree(ref_depth, ref_children, ref_filter)
        self.what_to_look_into()
        return "\n".join(self.lines)


def _derive_tree_path(input_path: str) -> str:
    """Default output path for the reference-tree JSON, next to the input file."""
    if input_path.endswith(".json"):
        return input_path[: -len(".json")] + ".tree.json"
    return input_path + ".tree.json"


def _derive_histogram_path(input_path: str) -> str:
    """Default output path for the histogram PNG, next to the input file."""
    if input_path.endswith(".json"):
        return input_path[: -len(".json")] + ".histogram.png"
    return input_path + ".histogram.png"


def _count_nodes(forest: list[dict[str, Any]]) -> int:
    """Total number of nodes in a reference-tree forest."""
    total = 0
    stack: list[dict[str, Any]] = list(forest)
    while stack:
        node = stack.pop()
        total += 1
        children = node.get("ch")
        if children:
            stack.extend(children)
    return total


def _wrap(text: str, width: int, indent: str) -> str:
    """Simple word wrap that keeps the first line un-indented."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if not lines:
        return ""
    return ("\n" + indent).join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="path to a gc-stats.json file")
    parser.add_argument("--top", type=int, default=20, help="number of top roots to show (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON instead of text")
    parser.add_argument(
        "--type-counts",
        action="store_true",
        help="show instance counts per type (requires a build with 'tc' support)",
    )
    parser.add_argument(
        "--type-filter",
        default="",
        metavar="PATTERN",
        help="substring filter for --type-counts (e.g. 'prozess.signal_bundler')",
    )
    parser.add_argument(
        "--top-types",
        type=int,
        default=50,
        help="number of types to show in --type-counts (default: 50)",
    )
    parser.add_argument(
        "--no-ref-tree",
        action="store_true",
        help="do not render the 'rt' type reference tree",
    )
    parser.add_argument(
        "--ref-depth",
        type=int,
        default=4,
        help="max depth of the reference tree to display (default: 4)",
    )
    parser.add_argument(
        "--ref-children",
        type=int,
        default=8,
        help="max children per node to display in the reference tree (default: 8)",
    )
    parser.add_argument(
        "--ref-filter",
        action="extend",
        nargs="*",
        default=[],
        metavar="PATTERN",
        help=(
            "substring filter(s) for reference-tree roots; a root is shown if its type "
            "matches any of the given patterns. May be repeated or take multiple values "
            "(e.g. --ref-filter signal_bundler dict, or --ref-filter dd.ds --ref-filter prozess)"
        ),
    )
    parser.add_argument(
        "--histogram-png",
        nargs="?",
        const="<derive>",
        default=None,
        metavar="PATH",
        help=(
            "render the per-type instance counts as a horizontal bar chart PNG (requires "
            "matplotlib and a build with 'tc' support). Honors --type-filter and --top-types. "
            "With no PATH, writes <input>.histogram.png next to the input."
        ),
    )
    parser.add_argument(
        "--tree-json",
        nargs="?",
        const="<derive>",
        default=None,
        metavar="PATH",
        help=(
            "write the reference tree as nested {type: {type: {...}}} JSON with type names "
            "resolved. With no PATH, writes <input>.tree.json next to the input."
        ),
    )
    args = parser.parse_args(argv)

    try:
        data = load_snapshot(args.path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Schema v2 emits the first-order reference graph instead of a materialized
    # tree; reconstruct an index-typed rt so the rest of the report is unchanged.
    if gc_stats_graph.is_graph_format(data):
        recon_depth = max(gc_stats_graph.DEFAULT_MAX_DEPTH, args.ref_depth + 1)
        data["rt"] = gc_stats_graph.reconstruct_indexed_forest(data, max_depth=recon_depth)

    report = Report(data)
    text = report.build(
        args.top,
        show_type_counts=args.type_counts,
        type_filter=args.type_filter,
        top_types=args.top_types,
        show_ref_tree=not args.no_ref_tree,
        ref_depth=args.ref_depth,
        ref_children=args.ref_children,
        ref_filter=args.ref_filter,
    )

    if args.tree_json is not None:
        out_path = _derive_tree_path(args.path) if args.tree_json == "<derive>" else args.tree_json
        report.write_tree_json(out_path)
        print(f"wrote reference tree JSON ({_count_nodes(report.rt)} nodes) to {out_path}", file=sys.stderr)

    if args.histogram_png is not None:
        png_path = _derive_histogram_path(args.path) if args.histogram_png == "<derive>" else args.histogram_png
        try:
            n = report.write_histogram_png(png_path, args.top_types, args.type_filter)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"wrote class histogram ({n} types) to {png_path}", file=sys.stderr)

    if args.json:
        print(json.dumps({"findings": report.findings}, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
