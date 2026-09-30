#!/usr/bin/env python
r"""Diff CPython OLD..NEW over inventory-derived paths and emit a worklist.

Usage::

    python scripts/cpython_delta/diff.py v3.14.0 v3.15.0a7
    python scripts/cpython_delta/diff.py v3.14.0 v3.15.0a7 --backtest
    python scripts/cpython_delta/diff.py v3.14.0 v3.15.0a7 \
        --cpython ~/dd/cpython --inventory scripts/cpython_delta/inventory.json

Outputs (under ``docs/cpython-diffs/`` by default):

* ``work_<old>_to_<new>.md``
* ``work_<old>_to_<new>.json``
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404
import sys
from typing import Any
from typing import Iterable
from typing import TextIO


_PKG_DIR: Path = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from common import DEFAULT_CPYTHON_ROOT  # noqa: E402
from common import FIXED_WATCH_PATHS  # noqa: E402
from common import REPO_ROOT  # noqa: E402
from inventory import build_inventory  # noqa: E402
from inventory import default_inventory_path  # noqa: E402
from inventory import write_inventory  # noqa: E402


_HUNK_HEADER_RE: re.Pattern[str] = re.compile(r"""^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:\s+(.*))?$""")
_DIFF_GIT_RE: re.Pattern[str] = re.compile(r"""^diff --git a/(.*) b/(.*)$""")
_SYMBOL_TOKEN_RE: re.Pattern[str] = re.compile(
    r"""\b(?P<sym>"""
    r"""FRAME_(?:CREATED|SUSPENDED|SUSPENDED_YIELD_FROM|SUSPENDED_YIELD_FROM_LOCKED|"""
    r"""EXECUTING|COMPLETED|CLEARED|OWNED_BY_\w+|STATE_\w+)|"""
    r"""_PyInterpreterFrame|_PyThreadStateImpl|_PyStackRef|PyStackRef_\w+|"""
    r"""Py_TAG_\w+|Py_INT_TAG|Py_TAGGED_SHIFT|BITS_TO_PTR_MASKED|"""
    r"""_Py_AsyncioDebug|_AsyncioDebug|Py_AsyncioModuleDebugOffsets|"""
    r"""base_frame|asyncio_tasks_head|asyncio_running_loop|"""
    r"""gi_frame_state|f_executable|localsplus|stackpointer|"""
    r"""TaskObj|FutureObj|sys\.monitoring|"""
    r"""_PyFrame_SafeGetCode|_PyFrame_SafeGetLasti|_PyFrame_StackPeek"""
    r""")\b"""
)

# Ground-truth rows for the 3.14 → 3.15a7 backtest (analysis + #19269/#19272).
BACKTEST_EXPECTED: tuple[dict[str, str], ...] = (
    {
        "id": "frame_state_renumber",
        "match": "FRAME_SUSPENDED_YIELD_FROM_LOCKED|FRAME_CREATED|FRAME_COMPLETED|FRAME_STATE_",
        "source": "analysis_314_to_315.md §1 / #19269 tasks.h",
    },
    {
        "id": "frame_owned_by_cstack",
        "match": "FRAME_OWNED_BY_CSTACK",
        "source": "analysis_314_to_315.md §2 / #19269 frame.cc",
    },
    {
        "id": "stackref_tags",
        "match": "Py_TAG_|PyStackRef_|_PyStackRef|Py_TAGGED_SHIFT",
        "source": "analysis_314_to_315.md §3",
    },
    {
        "id": "base_frame",
        "match": "base_frame",
        "source": "analysis_314_to_315.md §5",
    },
    {
        "id": "asyncio_debug_sym",
        "match": "_Py_AsyncioDebug|_AsyncioDebug",
        "source": "analysis_314_to_315.md §6",
    },
    {
        "id": "genobject_stack_peek",
        "match": "genobject|_PyFrame_StackPeek|stackpointer|gi_frame_state",
        "source": "tasks.h cites Objects/genobject.c / #19269",
    },
    {
        "id": "remote_debugging",
        "match": "_remote_debugging|AsyncioDebug|task_node",
        "source": "tasks.h cites Modules/_remote_debugging",
    },
    {
        "id": "asyncio_monitoring",
        "match": "sys\\.monitoring|instrumentation|Lib/asyncio|wrapping\\.wrap|asyncio private",
        "source": "#19272 _asyncio.py sys.monitoring path",
    },
)


@dataclass
class DiffHunk:
    path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class WorkItem:
    symbol: str
    change_kind: str
    priority: str
    inventory_key: str | None
    ddtrace_sites: list[dict[str, Any]]
    cpython_paths: list[str]
    suggested_guard: str
    suggested_test: str
    hunk_summaries: list[str]
    notes: str = ""


def _run_git(cpython: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    git_bin: str | None = shutil.which("git")
    if git_bin is None:
        raise SystemExit("git not found on PATH")
    return subprocess.run(  # nosec B603
        [git_bin, *args],
        cwd=str(cpython),
        check=False,
        capture_output=True,
        text=True,
    )


def ensure_cpython_tags(cpython: Path, old: str, new: str) -> None:
    if not (cpython / ".git").exists():
        raise SystemExit(f"CPython checkout not found at {cpython} (clone to ~/dd/cpython)")
    for tag in (old, new):
        probe: subprocess.CompletedProcess[str] = _run_git(cpython, ["rev-parse", "--verify", tag])
        if probe.returncode != 0:
            fetch: subprocess.CompletedProcess[str] = _run_git(cpython, ["fetch", "--tags", "--quiet"])
            if fetch.returncode != 0:
                raise SystemExit(f"git fetch --tags failed in {cpython}: {fetch.stderr}")
            probe = _run_git(cpython, ["rev-parse", "--verify", tag])
            if probe.returncode != 0:
                raise SystemExit(f"Unknown CPython ref {tag!r} in {cpython}")


def collect_diff_paths(inventory: dict[str, Any]) -> list[str]:
    paths: set[str] = set(FIXED_WATCH_PATHS)
    for p in inventory.get("cpython_paths", []):
        if isinstance(p, str) and p:
            paths.add(p)
    return sorted(paths)


def parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current_path: str = ""
    current: DiffHunk | None = None
    for line in diff_text.splitlines():
        git_match: re.Match[str] | None = _DIFF_GIT_RE.match(line)
        if git_match is not None:
            current_path = git_match.group(2)
            current = None
            continue
        hunk_match: re.Match[str] | None = _HUNK_HEADER_RE.match(line)
        if hunk_match is not None:
            current = DiffHunk(
                path=current_path,
                old_start=int(hunk_match.group(1)),
                old_count=int(hunk_match.group(2) or "1"),
                new_start=int(hunk_match.group(3)),
                new_count=int(hunk_match.group(4) or "1"),
                header=(hunk_match.group(5) or "").strip(),
            )
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith(("+", "-", " ")):
            current.lines.append(line)
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                for sym_match in _SYMBOL_TOKEN_RE.finditer(line):
                    sym: str = sym_match.group("sym")
                    if sym not in current.symbols:
                        current.symbols.append(sym)
    return hunks


def git_diff_paths(cpython: Path, old: str, new: str, paths: Iterable[str]) -> tuple[str, list[str]]:
    """Return (unified_diff, existing_paths_used)."""
    existing: list[str] = []
    for p in paths:
        # Directories and files: ask git if the path existed on either side.
        check: subprocess.CompletedProcess[str] = _run_git(
            cpython,
            ["ls-tree", "-r", "--name-only", new, "--", p],
        )
        check_old: subprocess.CompletedProcess[str] = _run_git(
            cpython,
            ["ls-tree", "-r", "--name-only", old, "--", p],
        )
        if check.stdout.strip() or check_old.stdout.strip() or (cpython / p).exists():
            existing.append(p)
    if not existing:
        return "", []
    result: subprocess.CompletedProcess[str] = _run_git(
        cpython,
        ["diff", "--no-ext-diff", f"{old}..{new}", "--", *existing],
    )
    if result.returncode not in (0, 1):
        raise SystemExit(f"git diff failed: {result.stderr}")
    return result.stdout, existing


def _classify_change(symbol: str, hunk_texts: list[str]) -> tuple[str, str]:
    """Return (change_kind, priority) heuristics."""
    joined: str = "\n".join(hunk_texts)
    removed: bool = any(
        line.startswith("-") and symbol in line and f"+{symbol}" not in joined for line in joined.splitlines()
    )
    added: bool = any(line.startswith("+") and symbol in line for line in joined.splitlines())
    # Enum renumber: same name on both sides of a #define or enum line.
    if symbol.startswith("FRAME_") and re.search(rf"[+-].*\b{re.escape(symbol)}\b.*=", joined):
        return "renumbered_enum", "breaks_build" if "OWNED_BY" not in symbol else "silent_misread"
    if symbol == "FRAME_OWNED_BY_CSTACK" and removed and not added:
        return "removed", "silent_misread"
    if symbol == "FRAME_COMPLETED" and removed:
        return "removed", "breaks_build"
    if symbol == "FRAME_SUSPENDED_YIELD_FROM_LOCKED" and added:
        return "new_field_or_enum", "silent_misread"
    if symbol == "base_frame" and added:
        return "new_field_shifting_offsets", "advisory"
    if "AsyncioDebug" in symbol and ("_Py_AsyncioDebug" in joined or "_AsyncioDebug" in joined):
        return "renamed", "advisory"
    if symbol.startswith("PyStackRef_") or symbol.startswith("Py_TAG_"):
        return "semantics_change", "silent_misread"
    if symbol == "sys.monitoring" or "instrumentation" in joined.lower():
        return "semantics_change", "breaks_build"
    if removed and not added:
        return "removed", "breaks_build"
    if added and not removed:
        return "new_field_or_enum", "advisory"
    if "moved" in joined.lower() or re.search(r"#include.*pycore_", joined):
        return "moved_header", "advisory"
    return "semantics_change", "advisory"


def _suggested_guard(old: str, new: str) -> str:
    """Best-effort PY_VERSION_HEX suggestion from the *new* tag."""
    # v3.15.0a7 → 0x030f0000; v3.16.0 → 0x03100000
    m: re.Match[str] | None = re.match(r"v?(\d+)\.(\d+)", new)
    if m is None:
        return f"#if PY_VERSION_HEX /* TODO: guard for {new} */"
    major: int = int(m.group(1))
    minor: int = int(m.group(2))
    hex_val: str = f"0x{major:02x}{minor:02x}0000"
    return f"#if PY_VERSION_HEX >= {hex_val}"


def _suggested_test(symbol: str, change_kind: str) -> str:
    if symbol.startswith("FRAME_") or "frame_state" in symbol.lower():
        return "test_frame_state_315.cpp / test_cpython_layout_contracts.cpp"
    if "TaskObj" in symbol or "asyncio" in symbol.lower() or "Asyncio" in symbol:
        return "test_task_traversal.cpp + asyncio hook probe (verify_profiler_compatibility)"
    if "StackRef" in symbol or symbol.startswith("Py_TAG_"):
        return "test_cpython_layout_contracts.cpp (stackref alignment)"
    if change_kind == "new_field_shifting_offsets":
        return "test_cpython_layout_contracts.cpp (offset asserts)"
    return "test_cpython_layout_contracts.cpp"


def _inventory_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map lowercased symbol tokens → inventory entries."""
    index: dict[str, dict[str, Any]] = {}
    for item in inventory.get("symbols", []):
        if not isinstance(item, dict):
            continue
        sym: str = str(item.get("symbol", ""))
        key: str = str(item.get("key", ""))
        index[sym.lower()] = item
        index[key.lower()] = item
        # Also index bare FRAME_* from keys like enum_macro:FRAME_X
        if ":" in key:
            index[key.split(":", 1)[1].lower()] = item
    return index


def _sites_for_symbol(index: dict[str, dict[str, Any]], symbol: str) -> tuple[str | None, list[dict[str, Any]]]:
    needle: str = symbol.lower()
    item: dict[str, Any] | None = index.get(needle)
    if item is None:
        # Prefer exact inventory ``symbol`` field equality over substring noise
        # (e.g. avoid matching FRAME_COMPLETED → field_access:frame).
        for v in index.values():
            inv_sym: str = str(v.get("symbol", "")).lower()
            if inv_sym == needle:
                item = v
                break
    if item is None and len(needle) >= 8:
        # Conservative fuzzy: inventory symbol contains the token as a whole word.
        word_re: re.Pattern[str] = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])")
        for v in index.values():
            inv_sym = str(v.get("symbol", ""))
            if word_re.search(inv_sym):
                item = v
                break
    if item is None:
        return None, []
    sites: list[Any] = list(item.get("sites", []))
    return str(item.get("key")), sites


def join_worklist(
    old: str,
    new: str,
    inventory: dict[str, Any],
    hunks: list[DiffHunk],
) -> dict[str, Any]:
    index: dict[str, dict[str, Any]] = _inventory_index(inventory)
    # Collect hunks per symbol that appear in the diff.
    by_symbol: dict[str, list[DiffHunk]] = {}
    for hunk in hunks:
        tokens: set[str] = set(hunk.symbols)
        # Always associate path-level awareness for inventory python_api rows
        # when Lib/asyncio or instrumentation changes.
        if hunk.path.startswith("Lib/asyncio") or hunk.path == "Python/instrumentation.c":
            tokens.add("sys.monitoring")
            tokens.add("asyncio private / task APIs")
            tokens.add("ddtrace.internal.wrapping.wrap")
        if "genobject" in hunk.path:
            tokens.add("gi_frame_state")
            tokens.add("_PyFrame_StackPeek")
        for sym in tokens:
            by_symbol.setdefault(sym, []).append(hunk)

    work_items: list[WorkItem] = []
    touched_inventory_keys: set[str] = set()
    for symbol, sym_hunks in sorted(by_symbol.items()):
        inv_key, sites = _sites_for_symbol(index, symbol)
        hunk_texts: list[str] = [h.text() for h in sym_hunks]
        change_kind, priority = _classify_change(symbol, hunk_texts)
        # Inventory miss: still emit as awareness if path is on the watch list.
        if not sites and inv_key is None:
            # Keep awareness rows for important CPython-only symbols.
            if not (
                symbol.startswith("FRAME_")
                or symbol.startswith("Py")
                or symbol.startswith("_")
                or symbol in {"base_frame", "sys.monitoring", "gi_frame_state", "stackpointer"}
            ):
                continue
            priority = "advisory"
        if inv_key is not None:
            touched_inventory_keys.add(inv_key)
        paths: list[str] = sorted({h.path for h in sym_hunks})
        summaries: list[str] = [
            f"{h.path}:@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@ {h.header}"
            for h in sym_hunks[:8]
        ]
        work_items.append(
            WorkItem(
                symbol=symbol,
                change_kind=change_kind,
                priority=priority if sites else "advisory",
                inventory_key=inv_key,
                ddtrace_sites=sites[:40],
                cpython_paths=paths,
                suggested_guard=_suggested_guard(old, new),
                suggested_test=_suggested_test(symbol, change_kind),
                hunk_summaries=summaries,
                notes="" if sites else "CPython change; no direct inventory site matched",
            )
        )

    # Inventory symbols not touched by any hunk → confirmed stable.
    stable: list[dict[str, Any]] = []
    for item in inventory.get("symbols", []):
        if not isinstance(item, dict):
            continue
        key: str = str(item.get("key", ""))
        if key in touched_inventory_keys:
            continue
        inv_symbol: str = str(item.get("symbol", ""))
        inv_symbol_l: str = inv_symbol.lower()
        if any(inv_symbol_l == s.lower() for s in by_symbol):
            continue
        stable.append(
            {
                "key": key,
                "symbol": inv_symbol,
                "kind": item.get("kind"),
                "site_count": len(item.get("sites", [])),
            }
        )

    # CPython hunks with no inventory intersection (awareness).
    awareness: list[dict[str, Any]] = []
    for hunk in hunks:
        if hunk.symbols and any(s in by_symbol for s in hunk.symbols):
            # Check whether any of those produced a work item with sites.
            continue
        if not hunk.symbols:
            awareness.append(
                {
                    "path": hunk.path,
                    "header": hunk.header,
                    "note": "diff hunk with no recognized symbols",
                }
            )

    # Priority sort: breaks_build > silent_misread > advisory
    prio_rank: dict[str, int] = {"breaks_build": 0, "silent_misread": 1, "advisory": 2}
    work_items.sort(key=lambda w: (prio_rank.get(w.priority, 9), w.symbol))

    return {
        "schema_version": 1,
        "old": old,
        "new": new,
        "work_item_count": len(work_items),
        "work_items": [asdict(w) for w in work_items],
        "stable_inventory": stable,
        "awareness_hunks_sample": awareness[:50],
        "hunk_count": len(hunks),
    }


def render_worklist_md(doc: dict[str, Any], recall: dict[str, Any] | None = None) -> str:
    lines: list[str] = [
        f"# CPython worklist: {doc['old']} → {doc['new']}",
        "",
        f"Generated by `scripts/cpython_delta/diff.py`. Hunks: {doc['hunk_count']}. "
        f"Work items: {doc['work_item_count']}.",
        "",
        "Agent next step: read each high/medium (`breaks_build` / `silent_misread`) hunk "
        "and the listed ddtrace call sites; confirm or downgrade; fill the fix plan.",
        "",
        "## Work items",
        "",
    ]
    for item in doc["work_items"]:
        if not isinstance(item, dict):
            continue
        lines.append(f"### `{item['symbol']}` — {item['change_kind']} ({item['priority']})")
        lines.append("")
        lines.append(f"- **Inventory key:** `{item.get('inventory_key')}`")
        lines.append(f"- **Suggested guard:** `{item['suggested_guard']}`")
        lines.append(f"- **Suggested test:** {item['suggested_test']}")
        if item.get("notes"):
            lines.append(f"- **Notes:** {item['notes']}")
        paths = item.get("cpython_paths") or []
        lines.append(f"- **CPython paths:** {', '.join(f'`{p}`' for p in paths)}")
        sites = item.get("ddtrace_sites") or []
        if sites:
            lines.append("- **ddtrace sites:**")
            for site in sites[:15]:
                lines.append(f"  - `{site['file']}:{site['line']}`")
            if len(sites) > 15:
                lines.append(f"  - … +{len(sites) - 15} more")
        summaries = item.get("hunk_summaries") or []
        if summaries:
            lines.append("- **Hunks:**")
            for s in summaries[:5]:
                lines.append(f"  - `{s}`")
        lines.append("")

    stable = doc.get("stable_inventory") or []
    lines.append(f"## Stable inventory symbols ({len(stable)})")
    lines.append("")
    lines.append("These inventory entries had no intersecting diff hunk:")
    lines.append("")
    for row in stable[:40]:
        lines.append(f"- `{row['key']}` ({row.get('kind')}, {row.get('site_count')} sites)")
    if len(stable) > 40:
        lines.append(f"- … +{len(stable) - 40} more")
    lines.append("")

    if recall is not None:
        lines.append("## Backtest recall")
        lines.append("")
        lines.append(
            f"Matched **{recall['matched']}/{recall['expected']}** "
            f"({recall['recall_pct']:.0f}% recall) against analysis_314_to_315.md + #19269/#19272."
        )
        lines.append("")
        for row in recall["rows"]:
            status: str = "HIT" if row["hit"] else "MISS"
            lines.append(f"- [{status}] `{row['id']}` — {row['source']}")
            if row.get("matched_symbols"):
                lines.append(f"  - symbols: {', '.join(f'`{s}`' for s in row['matched_symbols'][:8])}")
        lines.append("")

    lines.append("## Agent reading pass")
    lines.append("")
    lines.append(
        "For every `breaks_build` / `silent_misread` row: open the hunk in the CPython "
        "checkout, open each ddtrace site, confirm or downgrade priority, write the fix "
        "plan, and record open questions. Semantic changes (e.g. `PyFrameState` "
        "renumbering) need a human-level read, not a regex."
    )
    lines.append("")
    return "\n".join(lines)


def run_backtest_recall(work_doc: dict[str, Any]) -> dict[str, Any]:
    blob_parts: list[str] = []
    symbols_seen: list[str] = []
    for item in work_doc.get("work_items", []):
        if not isinstance(item, dict):
            continue
        symbols_seen.append(str(item.get("symbol", "")))
        blob_parts.append(json.dumps(item, sort_keys=True))
    for row in work_doc.get("stable_inventory", []):
        blob_parts.append(json.dumps(row, sort_keys=True))
    blob: str = "\n".join(blob_parts)
    # Also search path names in hunk summaries.
    rows_out: list[dict[str, Any]] = []
    matched: int = 0
    for expected in BACKTEST_EXPECTED:
        pattern: str = expected["match"]
        hits: list[str] = []
        for sym in symbols_seen:
            if re.search(pattern, sym):
                hits.append(sym)
        if not hits and re.search(pattern, blob):
            hits.append("(blob match)")
        hit: bool = bool(hits)
        if hit:
            matched += 1
        rows_out.append(
            {
                "id": expected["id"],
                "source": expected["source"],
                "hit": hit,
                "matched_symbols": hits,
            }
        )
    total: int = len(BACKTEST_EXPECTED)
    return {
        "expected": total,
        "matched": matched,
        "recall_pct": (100.0 * matched / total) if total else 0.0,
        "rows": rows_out,
    }


def worklist_basenames(old: str, new: str) -> tuple[str, str]:
    base: str = f"work_{old}_to_{new}"
    return f"{base}.md", f"{base}.json"


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    out_stream: TextIO = stdout if stdout is not None else sys.stdout
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", help="CPython old tag (e.g. v3.14.0)")
    parser.add_argument("new", help="CPython new tag (e.g. v3.15.0a7)")
    parser.add_argument(
        "--cpython",
        type=Path,
        default=DEFAULT_CPYTHON_ROOT,
        help="CPython git checkout (default: ~/dd/cpython)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="dd-trace-py repository root",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="inventory.json path (default: build fresh + write package default)",
    )
    parser.add_argument(
        "--refresh-inventory",
        action="store_true",
        help="rebuild inventory.json even if --inventory points at an existing file",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="directory for work_*.md/.json (default: docs/cpython-diffs)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="attach recall vs analysis_314_to_315.md + #19269/#19272 expectations",
    )
    args: argparse.Namespace = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    cpython: Path = args.cpython.expanduser().resolve()
    ensure_cpython_tags(cpython, args.old, args.new)

    inv_path: Path = args.inventory.resolve() if args.inventory is not None else default_inventory_path(repo_root)
    inventory: dict[str, Any]
    if args.refresh_inventory or not inv_path.exists() or args.inventory is None:
        inventory = build_inventory(repo_root)
        write_inventory(inventory, inv_path)
        out_stream.write(f"Wrote inventory {inv_path} ({inventory['symbol_count']} symbols)\n")
    else:
        inventory = json.loads(inv_path.read_text(encoding="utf-8"))

    paths: list[str] = collect_diff_paths(inventory)
    diff_text, used_paths = git_diff_paths(cpython, args.old, args.new, paths)
    out_stream.write(f"Diffing {len(used_paths)} paths in {cpython} ({args.old}..{args.new})\n")
    hunks: list[DiffHunk] = parse_unified_diff(diff_text)
    out_stream.write(f"Parsed {len(hunks)} hunks\n")

    work_doc: dict[str, Any] = join_worklist(args.old, args.new, inventory, hunks)
    recall: dict[str, Any] | None = None
    if args.backtest:
        recall = run_backtest_recall(work_doc)
        work_doc["backtest_recall"] = recall
        out_stream.write(f"Backtest recall: {recall['matched']}/{recall['expected']} ({recall['recall_pct']:.0f}%)\n")

    out_dir: Path = args.out_dir.resolve() if args.out_dir is not None else repo_root / "docs" / "cpython-diffs"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_name, json_name = worklist_basenames(args.old, args.new)
    md_path: Path = out_dir / md_name
    json_path: Path = out_dir / json_name
    json_path.write_text(json.dumps(work_doc, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_worklist_md(work_doc, recall=recall), encoding="utf-8")
    out_stream.write(f"Wrote {md_path}\nWrote {json_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
