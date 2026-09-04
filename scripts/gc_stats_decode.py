#!/usr/bin/env python3
"""Decode the string-table references in a GCMonitor ``gc-stats.json`` snapshot.

The producer (see
``ddtrace/internal/datadog/profiling/dd_wrapper/src/gc_monitor_json.cpp``) stores
type names once in a string table ``tt`` and references them everywhere else by
integer index via a ``"t"`` field:

    {"t": 215, "ic": 95059, "ts": 6083776, "ch": [ ... ]}

This script produces an *equivalent* JSON object with the same shape, except
every ``"t"`` index is replaced by the type-name string it points at, so the
``r`` (roots) and ``rt`` (reference tree) sections are readable on their own.
``tc`` (per-type instance count, parallel to ``tt``) is folded into a
name -> count map so it survives without the positional coupling to ``tt``.

Reference-tree expansion (on by default)
----------------------------------------
The producer bounds the ``rt`` forest with a per-root node budget, so a type
that appears deep in some other root's subtree is often rendered childless even
though it *does* retain other types (its children are simply truncated). The
``rt`` graph is a globally-aggregated holder-type -> held-type graph, so every
type has one canonical set of direct children -- the fullest occurrence, which
is normally its own root. By default this script rebuilds ``rt`` so every node
is re-expanded from that canonical map, i.e. childless occurrences are replaced
by the type's full children. Because the graph is cyclic and densely connected,
expansion is bounded by a per-path cycle guard (a type is never expanded twice
along the same chain; such a stop is marked ``"cyc": true``) plus a per-root node
cap, a depth cap, and a global safety cap (a cap stop is marked ``"cut": true``).
The budget is per root so dense hub types cannot starve the small application
roots. Use ``--no-expand`` to keep the original truncated tree.

By default (``--dedupe``) each type's subtree is expanded only once across the
whole forest; every later occurrence becomes a bare node marked ``"ref": true``
(its children live at the one expanded copy). Without this a type's subtree is
copied under every parent, which is multiplicative and can produce tens of
millions of near-duplicate nodes; ``--no-dedupe`` restores that behaviour.

After expansion, redundant top-level roots are dropped (a ``ref`` root under
dedupe, or a root whose type appears as a child otherwise). Use
``--keep-referenced-roots`` to keep them, or ``--root-filter PATTERN`` to expand
and keep only the roots you care about.

Usage::

    python3 scripts/gc_stats_decode.py gc-stats.json
    python3 scripts/gc_stats_decode.py gc-stats.json -o out.json
    python3 scripts/gc_stats_decode.py gc-stats.json --keep-string-table
    python3 scripts/gc_stats_decode.py gc-stats.json --no-expand
    python3 scripts/gc_stats_decode.py gc-stats.json --expand-max-depth 12
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import re
import sys
from typing import Any
from typing import Optional

import gc_stats_graph


# Default bounds for --expand. The type graph is cyclic and dense, so even with
# a per-path cycle guard an unbounded expansion is factorial in the worst case.
# The budget is applied *per root* (not globally) so that dense hub types like
# functools.partial cannot starve the small application roots we care about;
# a global cap only guards against a pathological total.
DEFAULT_EXPAND_MAX_DEPTH: int = 6
DEFAULT_EXPAND_MAX_NODES_PER_ROOT: int = 512
DEFAULT_EXPAND_MAX_TOTAL_NODES: int = 2_000_000


def resolve_type(idx: Any, type_table: list[str]) -> Any:
    """Map a ``t`` index to its type name, leaving unknown indices flagged."""
    if isinstance(idx, int) and 0 <= idx < len(type_table):
        return type_table[idx]
    return f"<type#{idx}>"


def decode_node(node: dict[str, Any], type_table: list[str]) -> dict[str, Any]:
    """Return a copy of a reference-tree node with ``t`` resolved, recursively."""
    out: dict[str, Any] = dict(node)
    if "t" in out:
        out["t"] = resolve_type(out["t"], type_table)
    children = out.get("ch")
    if children:
        out["ch"] = [decode_node(child, type_table) for child in children]
    return out


def decode_snapshot(data: dict[str, Any], keep_string_table: bool) -> dict[str, Any]:
    """Return an equivalent snapshot with all string-table references resolved.

    The positional arrays are folded into name-keyed maps: ``tc`` -> name -> count
    and ``tsz`` -> name -> shallow bytes. The v2 first-order graph (``g``/``roots``)
    is not copied verbatim; the caller reconstructs it (see main). Legacy ``rt``/``r``
    trees are decoded in place.
    """
    type_table: list[str] = data.get("tt", [])
    type_counts: list[int] = data.get("tc", [])
    type_sizes: list[int] = data.get("tsz", [])

    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == "tt":
            if keep_string_table:
                out["tt"] = value
            continue
        if key == "tc":
            # Fold the parallel tc array into a name -> count map so it no longer
            # depends on the positional string table.
            out["tc"] = {type_table[i]: type_counts[i] for i in range(min(len(type_table), len(type_counts)))}
            continue
        if key == "tsz":
            out["tsz"] = {type_table[i]: type_sizes[i] for i in range(min(len(type_table), len(type_sizes)))}
            continue
        if key in ("g", "roots"):
            # v2 first-order graph: reconstructed by the caller, not copied raw.
            continue
        if key == "r":
            out["r"] = [decode_node(root, type_table) for root in value]
            continue
        if key == "rt":
            out["rt"] = [decode_node(root, type_table) for root in value]
            continue
        out[key] = value
    return out


def build_canonical_children(rt: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    """Map each type name to its fullest set of direct children in the forest.

    Every (holder, held) edge is aggregated globally by the producer, so a type's
    direct children (and their ic/ts edge weights) are the same wherever the type
    appears -- except that a tight node budget can truncate the list. We therefore
    keep, per type, the occurrence with the most direct children.
    """
    canonical: dict[Any, list[dict[str, Any]]] = {}
    stack: list[dict[str, Any]] = list(rt)
    while stack:
        node = stack.pop()
        children = node.get("ch") or []
        if children:
            tname = node.get("t")
            direct = [{"t": c.get("t"), "ic": c.get("ic"), "ts": c.get("ts")} for c in children]
            prev = canonical.get(tname)
            if prev is None or len(direct) > len(prev):
                canonical[tname] = direct
            stack.extend(children)
    return canonical


def expand_forest(
    rt: list[dict[str, Any]],
    canonical: dict[Any, list[dict[str, Any]]],
    max_depth: int,
    max_nodes_per_root: int,
    max_total_nodes: int,
    dedupe: bool,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Rebuild the forest so every node shows its canonical children.

    With ``dedupe`` (recommended) each type's subtree is expanded only once
    across the whole forest; every later occurrence becomes a bare node marked
    ``"ref": true`` (its children live at the single expanded copy). This avoids
    the multiplicative blow-up of copying a type's subtree under every parent.

    Without ``dedupe`` the subtree is copied at every occurrence, bounded per
    root so no single dense root can starve the others; a type that recurses on
    a chain is stopped with ``"cyc": true``.

    Either way a depth/per-root/global cap stop is marked ``"cut": true``.
    Returns the new forest, the total node count, and whether the global cap hit.
    """
    total = 0
    hit_total_cap = False
    expanded_types: set[Any] = set()  # dedupe: types whose children were already emitted

    def build_root(root: dict[str, Any]) -> dict[str, Any]:
        # Level-order (BFS) expansion so that every node at depth d gets its
        # direct children reserved before any depth d+1 work. A per-node
        # depth-first walk would let an early sibling's deep subtree drain the
        # per-root budget and leave later nodes (e.g. SignalBundlerAuditLogger
        # under Bundle) childless even when the root as a whole is not full.
        nonlocal total, hit_total_cap
        node: dict[str, Any] = {"t": root.get("t"), "ic": root.get("ic"), "ts": root.get("ts")}
        total += 1
        root_used = 1
        # queue entries: (node, depth, path-of-ancestor-types-including-node)
        queue: deque[tuple[dict[str, Any], int, frozenset[Any]]] = deque([(node, 0, frozenset())])
        while queue:
            cur, depth, path = queue.popleft()
            if dedupe:
                if cur["t"] in expanded_types:
                    cur["ref"] = True  # already expanded elsewhere -- do not repeat its subtree
                    continue
                expanded_types.add(cur["t"])
            kids = canonical.get(cur["t"])
            if not kids or depth >= max_depth:
                continue
            child_path = path | {cur["t"]}
            children: list[dict[str, Any]] = []
            for kid in kids:
                if root_used >= max_nodes_per_root or total >= max_total_nodes:
                    cur["cut"] = True
                    hit_total_cap = hit_total_cap or total >= max_total_nodes
                    break
                total += 1
                root_used += 1
                child: dict[str, Any] = {"t": kid["t"], "ic": kid["ic"], "ts": kid["ts"]}
                children.append(child)
                if not dedupe and kid["t"] in child_path:
                    child["cyc"] = True  # type already on this chain -- do not recurse
                else:
                    queue.append((child, depth + 1, child_path))
            cur["ch"] = children
        return node

    forest = [build_root(root) for root in rt]
    return forest, total, hit_total_cap


def prune_referenced_roots(forest: list[dict[str, Any]], dedupe: bool) -> tuple[list[dict[str, Any]], int]:
    """Drop redundant top-level roots.

    With ``dedupe`` a root whose type was already expanded under an earlier root
    is itself a ``ref`` node with no unique content, so it is dropped. Otherwise
    a root whose type appears as a child anywhere is redundant (its subtree is
    visible under its referencer) and is dropped. Returns the pruned forest and
    the number of roots removed.
    """
    if dedupe:
        kept = [root for root in forest if not root.get("ref")]
        return kept, len(forest) - len(kept)

    referenced: set[Any] = set()
    for root in forest:
        stack: list[dict[str, Any]] = list(root.get("ch") or [])
        while stack:
            node = stack.pop()
            referenced.add(node["t"])
            children = node.get("ch")
            if children:
                stack.extend(children)
    kept = [root for root in forest if root["t"] not in referenced]
    return kept, len(forest) - len(kept)


def derive_output_path(input_path: str) -> str:
    """Default output path, next to the input file."""
    if input_path.endswith(".json"):
        return input_path[: -len(".json")] + ".decoded.json"
    return input_path + ".decoded.json"


def derive_html_path(input_path: str) -> str:
    """Default HTML output path, next to the input file."""
    if input_path.endswith(".json"):
        return input_path[: -len(".json")] + ".html"
    return input_path + ".html"


# Self-contained tree viewer. The decoded snapshot is embedded as JSON and the
# DOM is built lazily (children only when a node is expanded) so it stays
# responsive even with hundreds of thousands of nodes.
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; margin: 0; }
  header { position: sticky; top: 0; background: Canvas; border-bottom: 1px solid #8884; padding: 10px 14px; }
  h1 { font-size: 15px; margin: 0 0 6px; }
  .meta { color: #8a8a8a; font-size: 12px; }
  .controls { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  input[type=search] { font: inherit; padding: 4px 8px; min-width: 320px; }
  button { font: inherit; padding: 4px 8px; cursor: pointer; }
  #tree { padding: 8px 14px 60px; }
  ul { list-style: none; margin: 0; padding-left: 18px; }
  #tree > ul { padding-left: 0; }
  .row { display: flex; align-items: baseline; gap: 8px; padding: 1px 0; white-space: nowrap; }
  .row:hover { background: #8881; }
  .toggle { width: 1em; display: inline-block; text-align: center; cursor: pointer; color: #888; user-select: none; }
  .leaf > .row > .toggle { cursor: default; color: #ccc4; }
  .tname { cursor: pointer; }
  .tname:hover { text-decoration: underline; }
  .num { color: #8a8a8a; font-size: 12px; }
  .bytes { color: #2a7; }
  .badge { font-size: 10px; padding: 0 5px; border-radius: 8px; border: 1px solid #8886; color: #a55; }
  .badge.cyc { color: #77a; }
  mark { background: #fd6; color: #000; }
  .match > .row { background: #fd6a; }
  .matchName { font-weight: bold; }
  .badge.ref { color: #58a; border-color: #58a8; }
  .refName { color: #58a; cursor: pointer; }
  .refName:hover { text-decoration: underline; }
  .flash { animation: flash 1.6s ease-out; }
  @keyframes flash { from { background: #fd6; } to { background: transparent; } }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="meta" id="meta"></div>
  <div class="controls">
    <input type="search" id="filter" placeholder="type substring (regex ok), Enter to apply">
    <label class="meta"><input type="checkbox" id="wholeTree"> search whole tree (reveal paths)</label>
    <button id="expandAll">expand visible roots (1 level)</button>
    <button id="collapseAll">collapse all</button>
    <span class="meta" id="rootCount"></span>
  </div>
</header>
<div id="tree"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const ROOTS = (DATA.rt || []).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));

// Index every node once: parent pointers (for jump-to) and, per type, the single
// non-"ref" occurrence where its subtree is actually expanded (dedupe target).
const parentByNode = new Map();
const canonicalByType = new Map();
(function indexAll() {
  for (const root of ROOTS) {
    const stack = [[root, null]];
    while (stack.length) {
      const [n, par] = stack.pop();
      parentByNode.set(n, par);
      if (!n.ref && !canonicalByType.has(n.t)) canonicalByType.set(n.t, n);
      for (const c of (n.ch || [])) stack.push([c, n]);
    }
  }
})();
// node -> its currently-rendered <li> (rebuilt lazily; last render wins).
const liByNode = new Map();

function jumpTo(node) {
  if (!node) return;
  const path = []; let n = node;
  while (n) { path.unshift(n); n = parentByNode.get(n); }
  const rootLi = liByNode.get(path[0]);
  if (!rootLi || !rootLi.isConnected) {
    document.getElementById("filter").value = "";
    document.getElementById("wholeTree").checked = false;
    render(ROOTS);
  }
  let li = liByNode.get(path[0]);
  for (let i = 0; i < path.length - 1 && li; i++) {
    if (li.ensureOpen) li.ensureOpen();
    li = liByNode.get(path[i + 1]);
  }
  if (li) {
    li.scrollIntoView({ block: "center" });
    const row = li.querySelector(".row");
    if (row) { row.classList.add("flash"); setTimeout(() => row.classList.remove("flash"), 1600); }
  }
}

function hb(n) {
  if (n == null) return "";
  let v = n; const u = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]; let i = 0;
  while (Math.abs(v) >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return i === 0 ? v + " " + u[i] : v.toFixed(2) + " " + u[i];
}

function makeLi(node) {
  const li = document.createElement("li");
  liByNode.set(node, li);
  const kids = node.ch || [];
  const hasKids = kids.length > 0;
  if (!hasKids) li.className = "leaf";
  const row = document.createElement("div"); row.className = "row";
  const toggle = document.createElement("span"); toggle.className = "toggle";
  toggle.textContent = hasKids ? "\u25B6" : "\u00B7";
  const name = document.createElement("span"); name.className = "tname"; name.textContent = node.t;
  const stats = document.createElement("span"); stats.className = "num";
  stats.innerHTML = "ic=" + (node.ic ?? 0) + "  <span class='bytes'>" + hb(node.ts || 0) + "</span>"
    + (hasKids ? "  (" + kids.length + ")" : "");
  row.append(toggle, name, stats);
  if (node.cut) {
    const b = document.createElement("span"); b.className = "badge"; b.textContent = "cut"; row.append(b);
  }
  if (node.cyc) {
    const b = document.createElement("span"); b.className = "badge cyc"; b.textContent = "cyc"; row.append(b);
  }
  if (node.ref) {
    const b = document.createElement("span"); b.className = "badge ref"; b.textContent = "ref \u2192"; row.append(b);
    name.classList.add("refName");
    name.title = "expanded elsewhere -- click to jump to " + node.t;
    name.addEventListener("click", e => { e.stopPropagation(); jumpTo(canonicalByType.get(node.t)); });
  }
  li.append(row);

  let built = false, childUl = null, open = false;
  function toggleOpen() {
    if (!hasKids) return;
    open = !open;
    if (open && !built) {
      built = true;
      childUl = document.createElement("ul");
      kids.slice().sort((a, b) => (b.ts || 0) - (a.ts || 0)).forEach(c => childUl.append(makeLi(c)));
      li.append(childUl);
    }
    if (childUl) childUl.style.display = open ? "" : "none";
    toggle.textContent = open ? "\u25BC" : "\u25B6";
  }
  toggle.addEventListener("click", toggleOpen);
  if (!node.ref) name.addEventListener("click", toggleOpen);
  li.ensureOpen = () => { if (hasKids && !open) toggleOpen(); };
  li.openOneLevel = li.ensureOpen;
  return li;
}

const tree = document.getElementById("tree");
let currentRoots = ROOTS;

function render(roots) {
  tree.textContent = "";
  const ul = document.createElement("ul");
  roots.forEach(r => ul.append(makeLi(r)));
  tree.append(ul);
  document.getElementById("rootCount").textContent = roots.length + " roots shown";
}

function applyFilter(q) {
  if (!q) { currentRoots = ROOTS; render(currentRoots); return; }
  let re = null;
  try { re = new RegExp(q, "i"); } catch (e) { re = null; }
  currentRoots = ROOTS.filter(r => re ? re.test(r.t) : r.t.toLowerCase().includes(q.toLowerCase()));
  render(currentRoots);
}

const PATH_MATCH_LIMIT = 3000;

function tester(q) {
  try { const re = new RegExp(q, "i"); return t => re.test(t); }
  catch (e) { const lc = q.toLowerCase(); return t => t.toLowerCase().includes(lc); }
}

// Find every node whose type matches, anywhere in the forest, and reveal the
// full path(s) root -> ... -> match. Answers "I know the type but not the root".
function searchPaths(q) {
  const test = tester(q);
  const parent = new Map();
  const matches = [];
  for (const root of ROOTS) {
    const stack = [[root, null]];
    while (stack.length) {
      const [node, par] = stack.pop();
      parent.set(node, par);
      if (test(node.t)) matches.push(node);
      for (const c of (node.ch || [])) stack.push([c, node]);
    }
  }
  const matchSet = new Set(matches.slice(0, PATH_MATCH_LIMIT));
  const onPath = new Set();
  for (const m of matchSet) { let n = m; while (n && !onPath.has(n)) { onPath.add(n); n = parent.get(n); } }
  return { matchSet, onPath, total: matches.length };
}

function makePathLi(node, onPath, matchSet) {
  if (matchSet.has(node)) {
    const li = makeLi(node);
    li.classList.add("match");
    li.querySelector(".tname").classList.add("matchName");
    if (li.openOneLevel) li.openOneLevel();
    return li;
  }
  const li = document.createElement("li");
  const row = document.createElement("div"); row.className = "row";
  const toggle = document.createElement("span"); toggle.className = "toggle"; toggle.textContent = "\u25BC";
  const name = document.createElement("span"); name.className = "tname"; name.textContent = node.t;
  const stats = document.createElement("span"); stats.className = "num";
  stats.innerHTML = "ic=" + (node.ic ?? 0) + "  <span class='bytes'>" + hb(node.ts || 0) + "</span>";
  row.append(toggle, name, stats);
  li.append(row);
  const ul = document.createElement("ul");
  (node.ch || []).filter(c => onPath.has(c)).sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .forEach(c => ul.append(makePathLi(c, onPath, matchSet)));
  li.append(ul);
  let open = true;
  const t = () => {
    open = !open; ul.style.display = open ? "" : "none"; toggle.textContent = open ? "\u25BC" : "\u25B6";
  };
  toggle.addEventListener("click", t); name.addEventListener("click", t);
  return li;
}

function renderPaths(q) {
  const { matchSet, onPath, total } = searchPaths(q);
  tree.textContent = "";
  const ul = document.createElement("ul");
  ROOTS.filter(r => onPath.has(r)).sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .forEach(r => ul.append(makePathLi(r, onPath, matchSet)));
  tree.append(ul);
  let msg = total + " match(es) in " + ul.children.length + " root path(s)";
  if (total > PATH_MATCH_LIMIT) msg += " (showing first " + PATH_MATCH_LIMIT + ")";
  if (total === 0) msg += " -- not found (a pruned type may need --keep-referenced-roots)";
  document.getElementById("rootCount").textContent = msg;
}

function runSearch() {
  const q = document.getElementById("filter").value.trim();
  if (document.getElementById("wholeTree").checked) {
    if (q) renderPaths(q); else { currentRoots = ROOTS; render(currentRoots); }
  } else {
    applyFilter(q);
  }
}

document.getElementById("filter").addEventListener("keydown", e => { if (e.key === "Enter") runSearch(); });
document.getElementById("wholeTree").addEventListener("change", runSearch);
document.getElementById("expandAll").addEventListener("click", () => {
  tree.querySelectorAll("#tree > ul > li").forEach(li => li.openOneLevel && li.openOneLevel());
});
document.getElementById("collapseAll").addEventListener("click", () => render(currentRoots));

const gc = DATA.gc || {};
const ts = DATA.ts_ns ? new Date(DATA.ts_ns / 1e6).toISOString() : "?";
document.getElementById("meta").textContent =
  "snapshot " + ts + "  |  gc.enabled=" + gc.enabled + "  thresholds=" + JSON.stringify(gc.thresholds || [])
  + "  |  " + ROOTS.length + " roots (sorted by retained bytes)";
render(currentRoots);
</script>
</body>
</html>
"""


def write_html(decoded: dict[str, Any], path: str, title: str) -> None:
    """Write a self-contained, lazily-rendered HTML tree viewer for the snapshot."""
    payload = {"ts_ns": decoded.get("ts_ns"), "gc": decoded.get("gc"), "rt": decoded.get("rt", [])}
    data = json.dumps(payload, separators=(",", ":"))
    # Neutralize any sequence that could close the embedded <script> block.
    data = data.replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__DATA__", data).replace("__TITLE__", title)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="path to a gc-stats.json file")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="PATH",
        help="output path (default: <input>.decoded.json). Use '-' for stdout.",
    )
    parser.add_argument(
        "--keep-string-table",
        action="store_true",
        help="also keep the original 'tt' string table in the output",
    )
    parser.add_argument(
        "--no-expand",
        dest="expand",
        action="store_false",
        help="keep the original (budget-truncated) 'rt' tree instead of re-expanding every node",
    )
    parser.add_argument(
        "--expand-max-depth",
        type=int,
        default=DEFAULT_EXPAND_MAX_DEPTH,
        metavar="N",
        help=f"max depth when re-expanding 'rt' (default: {DEFAULT_EXPAND_MAX_DEPTH})",
    )
    parser.add_argument(
        "--expand-max-nodes-per-root",
        type=int,
        default=DEFAULT_EXPAND_MAX_NODES_PER_ROOT,
        metavar="N",
        help=f"per-root node cap when expanding 'rt' (default: {DEFAULT_EXPAND_MAX_NODES_PER_ROOT})",
    )
    parser.add_argument(
        "--expand-max-total-nodes",
        type=int,
        default=DEFAULT_EXPAND_MAX_TOTAL_NODES,
        metavar="N",
        help=f"global safety cap on total 'rt' nodes (default: {DEFAULT_EXPAND_MAX_TOTAL_NODES})",
    )
    parser.add_argument(
        "--keep-referenced-roots",
        dest="prune_referenced_roots",
        action="store_false",
        help="keep every root; by default a redundant/duplicate root is dropped",
    )
    parser.add_argument(
        "--no-dedupe",
        dest="dedupe",
        action="store_false",
        help="expand each type's subtree at every occurrence (huge); by default a type is expanded "
        "once and repeats become 'ref' nodes",
    )
    parser.add_argument(
        "--root-filter",
        default=None,
        metavar="PATTERN",
        help="only expand and keep roots whose type matches this substring/regex (focuses output on one subtree)",
    )
    parser.add_argument(
        "--html",
        nargs="?",
        const="<derive>",
        default=None,
        metavar="PATH",
        help="also write a self-contained HTML tree viewer (default path: <input>.html)",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indent (default: 2)")
    args = parser.parse_args(argv)

    try:
        with open(args.path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not isinstance(data, dict) or "gc" not in data:
        print(f"error: {args.path} does not look like a gc-stats snapshot", file=sys.stderr)
        return 1

    graph_format = gc_stats_graph.is_graph_format(data)
    decoded = decode_snapshot(data, keep_string_table=args.keep_string_table)

    # Determine the name-keyed canonical children map and the root seed nodes.
    # v2 snapshots carry the first-order graph directly; legacy v1 snapshots
    # carry an rt tree we derive the canonical map from.
    if graph_format:
        canonical, all_roots = gc_stats_graph.build_name_adjacency(data)
    else:
        all_roots = decoded.get("rt", []) if isinstance(decoded.get("rt"), list) else []
        canonical = build_canonical_children(all_roots)

    if not args.expand:
        if graph_format:
            # Emit the compact, lossless decoded graph (names resolved) instead
            # of a reconstructed tree.
            decoded["g"] = {holder: [[c["t"], c["ic"], c["ts"]] for c in kids] for holder, kids in canonical.items()}
            decoded["roots"] = [r["t"] for r in all_roots]
    elif all_roots:
        roots_to_expand = all_roots
        if args.root_filter:
            try:
                pattern = re.compile(args.root_filter)
            except re.error as exc:
                print(f"error: invalid --root-filter regex: {exc}", file=sys.stderr)
                return 1
            roots_to_expand = [r for r in all_roots if pattern.search(str(r.get("t", "")))]
            print(
                f"root-filter {args.root_filter!r}: {len(roots_to_expand)} of {len(all_roots)} roots match",
                file=sys.stderr,
            )
        decoded["rt"], node_count, capped = expand_forest(
            roots_to_expand,
            canonical,
            args.expand_max_depth,
            args.expand_max_nodes_per_root,
            args.expand_max_total_nodes,
            args.dedupe,
        )
        msg = (
            f"expanded rt: {node_count} nodes "
            f"(max-depth {args.expand_max_depth}, {args.expand_max_nodes_per_root} nodes/root, "
            f"dedupe={'on' if args.dedupe else 'off'})"
        )
        if capped:
            msg += f"; hit global --expand-max-total-nodes={args.expand_max_total_nodes}"
        print(msg, file=sys.stderr)

        if args.prune_referenced_roots:
            kept_before = len(decoded["rt"])
            decoded["rt"], removed = prune_referenced_roots(decoded["rt"], args.dedupe)
            print(
                f"pruned redundant roots: removed {removed} of {kept_before}, {len(decoded['rt'])} roots remain",
                file=sys.stderr,
            )

    if args.html is not None:
        html_path = derive_html_path(args.path) if args.html == "<derive>" else args.html
        write_html(decoded, html_path, title="GC snapshot: " + args.path.rsplit("/", 1)[-1])
        print(f"wrote HTML tree viewer to {html_path}", file=sys.stderr)

    if args.output == "-":
        json.dump(decoded, sys.stdout, indent=args.indent)
        sys.stdout.write("\n")
        return 0

    out_path = args.output if args.output is not None else derive_output_path(args.path)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(decoded, fh, indent=args.indent)
    print(f"wrote decoded snapshot to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
