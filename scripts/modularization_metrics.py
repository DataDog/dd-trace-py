#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Compute Robert C. Martin's package-level design metrics for ddtrace/.

Metrics per subsystem (first path segment under ddtrace/):

  Ca  — afferent coupling:  number of other subsystems that import this one
  Ce  — efferent coupling:  number of other subsystems this one imports from
  I   — instability:        Ce / (Ca + Ce)   [0 = stable, 1 = unstable]
  A   — abstractness:       abstract_classes / total_classes
                             (shown as — for subsystems with 0 classes; D is also — for shims)
  D   — signed distance:    A + I - 1
          D < 0  Zone of Pain        (stable but concrete — hard to change)
          D = 0  Main sequence       (ideal)
          D > 0  Zone of Uselessness (abstract but unstable — nobody uses it)
          —      Undefined           (0 classes — pure shim/re-export, metric N/A)

Modes:
  Default      Subsystem-level summary table (CI-suitable).
               Only writes output when at least one subsystem has |D| > --threshold.
  --drilldown  Per-module breakdown of a subsystem with class-level candidates.
               Intended for local use to decide what to work on next.

Run with ``uv run --script scripts/modularization_metrics.py [--drilldown SUBSYSTEM]``.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any

from _import_graph import collect_edges
from _import_graph import extract_ddtrace_imports
from _import_graph import path_bucket
from _import_graph import path_to_module


# ---------------------------------------------------------------------------
# Shared AST helpers — abstractness classification
# ---------------------------------------------------------------------------


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_attr(node: ast.expr, attr: str) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == attr


def _bases_include(bases: list[ast.expr], *names: str) -> bool:
    for base in bases:
        for name in names:
            if _is_name(base, name) or _is_attr(base, name):
                return True
    return False


def _keywords_include_abcmeta(keywords: list[ast.keyword]) -> bool:
    for kw in keywords:
        if kw.arg == "metaclass":
            if _is_name(kw.value, "ABCMeta") or _is_attr(kw.value, "ABCMeta"):
                return True
    return False


def _has_abstractmethod(class_node: ast.ClassDef) -> bool:
    for node in ast.walk(class_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if _is_name(dec, "abstractmethod") or _is_attr(dec, "abstractmethod"):
                    return True
    return False


def _classify_class(node: ast.ClassDef) -> str:
    """Return 'abstract', 'protocol', or 'concrete'."""
    if _bases_include(node.bases, "Protocol"):
        return "protocol"
    if _bases_include(node.bases, "ABC") or _keywords_include_abcmeta(node.keywords) or _has_abstractmethod(node):
        return "abstract"
    return "concrete"


# ---------------------------------------------------------------------------
# Summary-level analysis (subsystem buckets)
# ---------------------------------------------------------------------------


def analyze_abstractness(ddtrace_root: Path) -> dict[str, dict[str, int]]:
    """Count classes per top-level subsystem bucket."""
    counts: dict[str, dict[str, int]] = {}
    skip = {"vendor", "__pycache__", ".pytest_cache", "test-results"}
    for py in sorted(ddtrace_root.rglob("*.py")):
        rel_parts = py.relative_to(ddtrace_root).parts
        if rel_parts and rel_parts[0] in skip:
            continue
        if "__pycache__" in rel_parts:
            continue
        if any(p.startswith(".") for p in rel_parts):
            continue
        bucket = path_bucket(py, ddtrace_root, ())
        if bucket is None:
            continue
        if bucket not in counts:
            counts[bucket] = {"total": 0, "abstract": 0, "protocol": 0, "files": 0}
        counts[bucket]["files"] += 1
        try:
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                counts[bucket]["total"] += 1
                kind = _classify_class(node)
                if kind != "concrete":
                    counts[bucket][kind] += 1
    return counts


def compute_coupling(edges: set[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Ca / Ce per subsystem from a directed edge set (src imports dst)."""
    all_nodes: set[str] = set()
    for src, dst in edges:
        all_nodes.add(src)
        all_nodes.add(dst)
    ca: dict[str, int] = {n: 0 for n in all_nodes}
    ce: dict[str, int] = {n: 0 for n in all_nodes}
    for src, dst in edges:
        ce[src] += 1
        ca[dst] += 1
    return {n: {"Ca": ca[n], "Ce": ce[n]} for n in all_nodes}


def _martin_record(sub: str, ca: int, ce: int, ab: dict[str, Any]) -> dict[str, Any]:
    total_coupling = ca + ce
    I = ce / total_coupling if total_coupling > 0 else 0.5  # noqa: E741
    total_classes = ab.get("total", 0)
    abstract_classes = ab.get("abstract", 0) + ab.get("protocol", 0)
    A: float | None
    D: float | None
    if total_classes > 0:
        a = abstract_classes / total_classes
        A = round(a, 3)
        D = round(a + I - 1.0, 3)
    else:
        A = None  # 0/0: undefined, not zero — subsystem is a shim with no classes
        D = None
    return {
        "subsystem": sub,
        "files": ab.get("files", 0),
        "classes": total_classes,
        "abstract_classes": abstract_classes,
        "Ca": ca,
        "Ce": ce,
        "I": round(I, 3),
        "A": A,
        "D": D,
    }


def compute_metrics(ddtrace_root: Path, min_edge_weight: int = 0) -> list[dict[str, Any]]:
    """One record per subsystem with all Martin metrics."""
    edges, _ = collect_edges(
        ddtrace_root,
        (),
        min_dependents_per_node=0,
        min_edge_weight=min_edge_weight,
        collect_aliases=False,
    )
    coupling = compute_coupling(edges)
    abstractness = analyze_abstractness(ddtrace_root)
    records = []
    for sub in sorted(set(coupling) | set(abstractness)):
        cp = coupling.get(sub, {"Ca": 0, "Ce": 0})
        ab = abstractness.get(sub, {"total": 0, "abstract": 0, "protocol": 0, "files": 0})
        records.append(_martin_record(sub, cp["Ca"], cp["Ce"], ab))
    return records


# ---------------------------------------------------------------------------
# Drilldown-level analysis (modules within one subsystem)
# ---------------------------------------------------------------------------

_SKIP = {"vendor", "__pycache__", ".pytest_cache", "test-results"}


def _drilldown_src_bucket(py: Path, ddtrace_root: Path, subsystem: str) -> str | None:
    """Bucket for a source file: first path segment inside ddtrace/{subsystem}/."""
    sub_dir = ddtrace_root / subsystem
    try:
        rel = py.relative_to(sub_dir)
    except ValueError:
        return None
    parts = rel.parts
    top = parts[0]
    if top == "__pycache__":
        return None
    if top == "__init__.py":
        return f"ddtrace.{subsystem}"
    return f"ddtrace.{subsystem}.{top[:-3] if top.endswith('.py') else top}"


def _drilldown_dst_bucket(imp: str, subsystem: str) -> str | None:
    """Map an imported ddtrace module name to a drilldown-level bucket."""
    if not imp.startswith("ddtrace."):
        return None
    rest = imp[len("ddtrace.") :]
    parts = rest.split(".")
    if not parts or not parts[0]:
        return None
    if parts[0] == subsystem:
        # Internal: ddtrace.internal.foo.bar → ddtrace.internal.foo
        if len(parts) >= 2:
            return f"ddtrace.{parts[0]}.{parts[1]}"
        return f"ddtrace.{parts[0]}"
    # External: ddtrace.other.anything → ddtrace.other
    return f"ddtrace.{parts[0]}"


def _build_module_imports(ddtrace_root: Path) -> dict[str, set[str]]:
    """Scan all files; return {module_name: set_of_imported_ddtrace_modules}."""
    result: dict[str, set[str]] = {}
    for py in sorted(ddtrace_root.rglob("*.py")):
        rel_parts = py.relative_to(ddtrace_root).parts
        if rel_parts and rel_parts[0] in _SKIP:
            continue
        if "__pycache__" in rel_parts:
            continue
        if any(p.startswith(".") for p in rel_parts):
            continue
        mod, is_pkg_init = path_to_module(ddtrace_root, py)
        try:
            source = py.read_text(encoding="utf-8")
        except OSError:
            continue
        result[mod] = extract_ddtrace_imports(source, mod, is_pkg_init, False, frozenset())
    return result


def _top_bucket(module: str) -> str:
    """ddtrace.internal.foo → ddtrace.internal"""
    if not module.startswith("ddtrace."):
        return module
    rest = module[len("ddtrace.") :]
    return f"ddtrace.{rest.split('.')[0]}"


def compute_drilldown_coupling(ddtrace_root: Path, subsystem: str) -> dict[str, dict[str, int]]:
    """Ca/Ce per drilldown bucket (module within subsystem).

    Ce[b] = distinct drilldown-level or top-level buckets that b imports from.
    Ca[b] = distinct drilldown-level or top-level buckets that import b.
    """
    module_imports = _build_module_imports(ddtrace_root)

    # Map every module to its bucket in this drilldown view
    def bucket_of(mod: str) -> str:
        py = ddtrace_root / Path(*mod[len("ddtrace.") :].split("."))
        # Try __init__.py path too
        b = _drilldown_src_bucket(py.with_suffix(".py"), ddtrace_root, subsystem)
        if b is None:
            b = _drilldown_src_bucket(py / "__init__.py", ddtrace_root, subsystem)
        return b or _top_bucket(mod)

    prefix = f"ddtrace.{subsystem}"
    ce_sets: dict[str, set[str]] = defaultdict(set)
    ca_sets: dict[str, set[str]] = defaultdict(set)

    for mod, imports in module_imports.items():
        src_b = bucket_of(mod)
        for imp in imports:
            dst_b = _drilldown_dst_bucket(imp, subsystem)
            if dst_b is None or dst_b == src_b:
                continue
            # Track Ce for subsystem source buckets
            if src_b.startswith(prefix):
                ce_sets[src_b].add(dst_b)
            # Track Ca for subsystem destination buckets
            if dst_b.startswith(prefix):
                ca_sets[dst_b].add(src_b)

    all_buckets = set(ce_sets) | set(ca_sets)
    return {b: {"Ca": len(ca_sets.get(b, set())), "Ce": len(ce_sets.get(b, set()))} for b in all_buckets}


def analyze_drilldown_abstractness(ddtrace_root: Path, subsystem: str) -> dict[str, dict[str, Any]]:
    """Class counts + concrete class details per drilldown bucket."""
    counts: dict[str, dict[str, Any]] = {}
    sub_dir = ddtrace_root / subsystem
    if not sub_dir.is_dir():
        return counts

    for py in sorted(sub_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        bucket = _drilldown_src_bucket(py, ddtrace_root, subsystem)
        if bucket is None:
            continue
        if bucket not in counts:
            counts[bucket] = {"total": 0, "abstract": 0, "protocol": 0, "files": 0, "concrete_classes": []}
        counts[bucket]["files"] += 1
        try:
            source = py.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            counts[bucket]["total"] += 1
            kind = _classify_class(node)
            if kind == "concrete":
                public_methods = [
                    n.name
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("__")
                ]
                counts[bucket]["concrete_classes"].append(
                    {
                        "name": node.name,
                        "file": str(py.relative_to(ddtrace_root)),
                        "methods": len(public_methods),
                    }
                )
            else:
                counts[bucket][kind] += 1
    return counts


def compute_drilldown_metrics(ddtrace_root: Path, subsystem: str) -> list[dict[str, Any]]:
    """Per-module metrics for a subsystem, sorted by |D| descending."""
    coupling = compute_drilldown_coupling(ddtrace_root, subsystem)
    abstractness = analyze_drilldown_abstractness(ddtrace_root, subsystem)

    all_buckets = set(abstractness)  # only show buckets we actually scanned
    records = []
    for b in sorted(all_buckets):
        cp = coupling.get(b, {"Ca": 0, "Ce": 0})
        ab = abstractness[b]
        r = _martin_record(b, cp["Ca"], cp["Ce"], ab)
        r["concrete_classes"] = ab.get("concrete_classes", [])
        records.append(r)
    return sorted(records, key=lambda r: abs(r["D"]) if r["D"] is not None else -1, reverse=True)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _d_cell(d: float | None, threshold: float) -> tuple[str, str]:
    """Return (row_flag, formatted_D_cell) for a D value.  None → shim, never flagged."""
    if d is None:
        return "", "—"
    d_str = f"{d:+.2f}"
    if abs(d) > threshold:
        return " ⚠️", f"**{d_str}**"
    return "", d_str


def format_markdown(records: list[dict[str, Any]], threshold: float) -> str:
    """Summary-level report. Returns empty string if no subsystem exceeds threshold."""
    visible = [r for r in records if r["files"] > 0]
    if not any(r["D"] is not None and abs(r["D"]) > threshold for r in visible):
        return ""

    # Shims (D=None) sort last; otherwise sort by |D| descending.
    sorted_records = sorted(visible, key=lambda r: abs(r["D"]) if r["D"] is not None else -1, reverse=True)
    lines = [
        "## Modularization metrics",
        "",
        f"> Threshold |D| > {threshold:.1f}  "
        "· **I** = instability (0 stable → 1 unstable)  "
        "· **A** = abstractness  "
        "· **D** = A+I−1 (negative = Pain · positive = Uselessness · ⚠️ = |D| > threshold · — = shim)",
        "",
        "| Subsystem | Files | Classes | Ca | Ce | I | A | D |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted_records:
        name = r["subsystem"].replace("ddtrace.", "", 1)
        flag, cell = _d_cell(r["D"], threshold)
        a_str = _fmt(r["A"]) if r["A"] is not None else "—"
        lines.append(
            f"| `{name}`{flag} | {r['files']} | {r['classes']} "
            f"| {r['Ca']} | {r['Ce']} "
            f"| {_fmt(r['I'])} | {a_str} | {cell} |"
        )
    flagged = [
        r["subsystem"].replace("ddtrace.", "", 1)
        for r in sorted_records
        if r["D"] is not None and abs(r["D"]) > threshold
    ]
    lines += [
        "",
        "**Drilldown** — run locally for per-module metrics and abstraction candidates:",
        "```",
        *[f"uv run --script scripts/modularization_metrics.py --drilldown {name}" for name in flagged],
        "```",
        "",
        "<details><summary>Metric definitions</summary>",
        "",
        "```",
        "Ca  afferent coupling — subsystems that depend on this one (fan-in)",
        "Ce  efferent coupling — subsystems this one depends on (fan-out)",
        "I   Ce / (Ca + Ce)   — instability [0 stable, 1 unstable]",
        "A   abstract_classes / total_classes — abstractness (— if 0 classes)",
        "D   A + I - 1        — signed distance from main sequence (— if 0 classes)",
        "      D < 0  Zone of Pain        (stable but concrete — hard to change)",
        "      D = 0  Main sequence       (ideal balance)",
        "      D > 0  Zone of Uselessness (abstract but unstable — nobody uses it)",
        "      —      Shim / facade       (0 classes; D undefined, never flagged)",
        "```",
        "",
        "Abstract classes: ABC subclasses, ABCMeta metaclass, @abstractmethod, typing.Protocol.",
        "",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def format_drilldown_markdown(records: list[dict[str, Any]], subsystem: str, threshold: float) -> str:
    """Detailed per-module report for a subsystem."""
    prefix = f"ddtrace.{subsystem}."
    lines = [
        f"## Drilldown — ddtrace/{subsystem}/",
        "",
        f"> Per-module metrics within `ddtrace/{subsystem}/`  "
        "· **D** = A+I−1 (negative = Pain · positive = Uselessness · ⚠️ = |D| > threshold)",
        "",
        "| Module | Files | Classes | Ca | Ce | I | A | D |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        name = r["subsystem"].replace(prefix, "", 1)
        flag, cell = _d_cell(r["D"], threshold)
        a_str = _fmt(r["A"]) if r["A"] is not None else "—"
        lines.append(
            f"| `{name}`{flag} | {r['files']} | {r['classes']} "
            f"| {r['Ca']} | {r['Ce']} "
            f"| {_fmt(r['I'])} | {a_str} | {cell} |"
        )

    # Abstraction candidates: concrete classes in Zone of Pain modules
    pain = [r for r in records if r["D"] is not None and r["D"] < -threshold and r.get("concrete_classes")]
    if pain:
        lines += [
            "",
            "### Abstraction candidates",
            "",
            "Concrete classes in Zone of Pain modules (D < "
            f"-{threshold:.1f}). "
            "Consider introducing ABCs or Protocols to reduce stability without losing rigidity.",
            "",
        ]
        for r in pain:
            name = r["subsystem"].replace(prefix, "", 1)
            lines += [
                f"**`{name}`** — D={r['D']:+.2f}, I={_fmt(r['I'])}, A={_fmt(r['A'])}",
                "",
            ]
            candidates = sorted(r["concrete_classes"], key=lambda c: c["methods"], reverse=True)
            for cls in candidates[:8]:
                lines.append(f"- `{cls['name']}` ({cls['methods']} public methods) — `{cls['file']}`")
            if len(candidates) > 8:
                lines.append(f"- _…and {len(candidates) - 8} more_")
            lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        metavar="T",
        help="Only write the summary report when at least one subsystem has |D| > T (default: 0.5).",
    )
    parser.add_argument(
        "--drilldown",
        metavar="SUBSYSTEM",
        help=(
            "Produce a per-module drilldown for SUBSYSTEM (e.g. 'internal'). "
            "Prints to stdout or --markdown FILE. Skips the summary report."
        ),
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="Write full metrics as JSON to FILE (summary or drilldown depending on mode).",
    )
    parser.add_argument(
        "--markdown",
        metavar="FILE",
        help="Write the report to FILE instead of stdout.",
    )
    parser.add_argument(
        "--min-edge-weight",
        type=int,
        default=0,
        metavar="W",
        help="Drop coupling edges with import-count weight < W (default: 0).",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    ddtrace_root = repo / "ddtrace"
    if not ddtrace_root.is_dir():
        print("ddtrace package not found", file=sys.stderr)
        return 1

    if args.drilldown:
        records = compute_drilldown_metrics(ddtrace_root, args.drilldown)
        md = format_drilldown_markdown(records, args.drilldown, args.threshold)
    else:
        records = compute_metrics(ddtrace_root, min_edge_weight=args.min_edge_weight)
        md = format_markdown(records, threshold=args.threshold)
        if not md:
            print(
                f"No subsystems with |D| > {args.threshold:.1f} — nothing to report.",
                file=sys.stderr,
            )

    if args.json:
        # Strip concrete_classes from JSON output (too verbose for summary)
        json_records = [{k: v for k, v in r.items() if k != "concrete_classes"} for r in records]
        Path(args.json).write_text(json.dumps(json_records, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}", file=sys.stderr)

    if md:
        if args.markdown:
            Path(args.markdown).write_text(md, encoding="utf-8")
            print(f"Wrote {args.markdown}", file=sys.stderr)
        else:
            print(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
