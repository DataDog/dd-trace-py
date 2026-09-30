#!/usr/bin/env python
"""Scan profiling sources for CPython symbols ddtrace depends on.

Writes ``scripts/cpython_delta/inventory.json`` (committed so reviewers see drift).

Usage::

    python scripts/cpython_delta/inventory.py
    python scripts/cpython_delta/inventory.py --out /tmp/inventory.json
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import re
import sys
from typing import Iterable
from typing import Iterator
from typing import TextIO


# Allow ``python scripts/cpython_delta/inventory.py`` without installing a package.
_PKG_DIR: Path = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from common import INVENTORY_SCAN_ROOTS  # noqa: E402
from common import REPO_ROOT  # noqa: E402
from common import SOURCE_SUFFIXES  # noqa: E402


_INCLUDE_RE: re.Pattern[str] = re.compile(
    r"""^\s*#\s*include\s*[<"](?P<header>(?:internal/pycore_[^">]+|cpython/[^">]+))[">]""",
)
_OFFSETOF_RE: re.Pattern[str] = re.compile(
    r"""offsetof\s*\(\s*(?P<typ>[_A-Za-z]\w*)\s*,\s*(?P<field>[_A-Za-z]\w*)\s*\)""",
)
_ARROW_RE: re.Pattern[str] = re.compile(
    r"""(?:->|\.)\s*(?P<field>[_A-Za-z]\w+)\b""",
)
_FRAME_MACRO_RE: re.Pattern[str] = re.compile(
    r"""\b(?P<sym>FRAME_(?:CREATED|SUSPENDED|SUSPENDED_YIELD_FROM|SUSPENDED_YIELD_FROM_LOCKED|"""
    r"""EXECUTING|COMPLETED|CLEARED|OWNED_BY_\w+|STATE_\w+))\b""",
)
_PY_HEX_RE: re.Pattern[str] = re.compile(
    r"""PY_VERSION_HEX\s*(?P<op>>=|<=|>|<|==|!=)\s*(?P<hex>0x[0-9a-fA-F]+)""",
)
_STRUCT_TYPE_RE: re.Pattern[str] = re.compile(
    r"""\b(?P<typ>(?:_PyInterpreterFrame|_PyThreadStateImpl|PyThreadState|PyGenObject|"""
    r"""PyCodeObject|_PyStackRef|TaskObj|FutureObj|llist_node|PyFrameObject))\b""",
)
_MIRROR_COMMENT_RE: re.Pattern[str] = re.compile(
    r"""(?P<hint>(?:TaskObj|FutureObj|genobject|_asynciomodule|mirrored?\s+from|"""
    r"""Objects/genobject\.c|Modules/_asynciomodule\.c|Modules/_remote_debugging))""",
    re.IGNORECASE,
)
_PY_VERSION_INFO_RE: re.Pattern[str] = re.compile(
    r"""sys\.version_info\s*(?P<op>>=|<=|>|<|==|!=)\s*\((?P<maj>\d+)\s*,\s*(?P<min>\d+)""",
)
_SYS_MONITORING_RE: re.Pattern[str] = re.compile(
    r"""\b(?:sys\.monitoring|_monitoring|MonitoringEventHandler|PY_RETURN|_register_return_hook)\b""",
)
_ASYNCIO_PRIVATE_RE: re.Pattern[str] = re.compile(
    r"""(?:asyncio\.tasks\.|_scheduled_tasks|_eager_tasks|_GatheringFuture|"""
    r"""taskgroups|create_task|set_event_loop|TaskGroup)\b""",
)
_STACKREF_API_RE: re.Pattern[str] = re.compile(
    r"""\b(?P<sym>PyStackRef_\w+|BITS_TO_PTR_MASKED|Py_TAG_\w+|Py_INT_TAG|Py_TAGGED_SHIFT)\b""",
)
_DEBUG_SYM_RE: re.Pattern[str] = re.compile(
    r"""\b(?P<sym>_Py_AsyncioDebug|_AsyncioDebug|Py_AsyncioModuleDebugOffsets)\b""",
)

# Fields that are CPython-layout sensitive when accessed via -> or .
_LAYOUT_FIELDS: frozenset[str] = frozenset(
    {
        "f_executable",
        "f_frame_state",
        "gi_frame_state",
        "gi_iframe",
        "localsplus",
        "stackpointer",
        "previous",
        "owner",
        "frame",
        "asyncio_tasks_head",
        "asyncio_running_loop",
        "base_frame",
        "task_name",
        "task_coro",
        "task_awaited_by",
        "task_node",
        "task_is_task",
        "task_awaited_by_is_set",
        "task_must_cancel",
        "task_fut_waiter",
        "co_nlocalsplus",
        "co_code_adaptive",
        "cframe",
        "current_frame",
    }
)


@dataclass(frozen=True)
class Site:
    """One ddtrace file:line that references a symbol."""

    file: str
    line: int


@dataclass
class SymbolEntry:
    """One inventoried CPython dependency."""

    symbol: str
    kind: str
    sites: list[Site] = field(default_factory=list)
    cpython_paths: list[str] = field(default_factory=list)
    notes: str = ""

    def add_site(self, rel_file: str, line_no: int) -> None:
        site: Site = Site(file=rel_file, line=line_no)
        if site not in self.sites:
            self.sites.append(site)


def _iter_source_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix in SOURCE_SUFFIXES:
                yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            # Skip vendored non-profiling noise under echion tests blobs if any.
            parts: tuple[str, ...] = path.parts
            if "node_modules" in parts or "__pycache__" in parts:
                continue
            yield path


def _header_to_cpython_path(header: str) -> str:
    """Map an include name to a CPython tree path."""
    if header.startswith("internal/"):
        return f"Include/{header}"
    if header.startswith("cpython/"):
        return f"Include/{header}"
    return header


def _mirror_paths_for_hint(hint: str) -> list[str]:
    lower: str = hint.lower()
    paths: list[str] = []
    if "genobject" in lower:
        paths.extend(["Objects/genobject.c", "Include/cpython/genobject.h"])
    if "asynciomodule" in lower or "taskobj" in lower or "futureobj" in lower:
        paths.extend(
            [
                "Modules/_asynciomodule.c",
                "Modules/_remote_debugging",
                "Include/internal/pycore_debug_offsets.h",
            ]
        )
    if "remote_debugging" in lower:
        paths.append("Modules/_remote_debugging")
    return paths


def scan_file(path: Path, repo_root: Path, entries: dict[str, SymbolEntry]) -> None:
    """Extract symbols from one source file into ``entries`` keyed by kind:symbol."""
    rel: str = str(path.relative_to(repo_root))
    try:
        text: str = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    lines: list[str] = text.splitlines()
    is_python: bool = path.suffix == ".py"

    for line_no, line in enumerate(lines, start=1):
        for match in _INCLUDE_RE.finditer(line):
            header: str = match.group("header")
            key: str = f"include:{header}"
            entry: SymbolEntry = entries.setdefault(
                key,
                SymbolEntry(
                    symbol=header,
                    kind="include",
                    cpython_paths=[_header_to_cpython_path(header)],
                ),
            )
            entry.add_site(rel, line_no)

        for match in _OFFSETOF_RE.finditer(line):
            typ: str = match.group("typ")
            fld: str = match.group("field")
            sym: str = f"{typ}.{fld}"
            key = f"field:{sym}"
            entry = entries.setdefault(
                key,
                SymbolEntry(symbol=sym, kind="struct_field", notes="offsetof"),
            )
            entry.add_site(rel, line_no)

        if not is_python:
            for match in _ARROW_RE.finditer(line):
                fld = match.group("field")
                if fld not in _LAYOUT_FIELDS:
                    continue
                key = f"field_access:{fld}"
                entry = entries.setdefault(
                    key,
                    SymbolEntry(symbol=fld, kind="struct_field", notes="member access"),
                )
                entry.add_site(rel, line_no)

        for match in _FRAME_MACRO_RE.finditer(line):
            sym = match.group("sym")
            key = f"enum_macro:{sym}"
            entry = entries.setdefault(
                key,
                SymbolEntry(
                    symbol=sym,
                    kind="enum_macro",
                    cpython_paths=[
                        "Include/internal/pycore_frame.h",
                        "Include/internal/pycore_interpframe_structs.h",
                    ],
                ),
            )
            entry.add_site(rel, line_no)

        for match in _PY_HEX_RE.finditer(line):
            sym = f"PY_VERSION_HEX {match.group('op')} {match.group('hex')}"
            key = f"version_guard:{sym}"
            entry = entries.setdefault(
                key,
                SymbolEntry(symbol=sym, kind="version_guard"),
            )
            entry.add_site(rel, line_no)

        for match in _STRUCT_TYPE_RE.finditer(line):
            typ = match.group("typ")
            key = f"type:{typ}"
            paths: list[str] = []
            if typ in {"TaskObj", "FutureObj"}:
                paths = [
                    "Modules/_asynciomodule.c",
                    "Modules/_remote_debugging",
                ]
            elif typ in {"_PyInterpreterFrame", "PyFrameObject"}:
                paths = [
                    "Include/internal/pycore_frame.h",
                    "Include/internal/pycore_interpframe_structs.h",
                ]
            elif typ == "_PyStackRef":
                paths = ["Include/internal/pycore_stackref.h"]
            elif typ in {"_PyThreadStateImpl", "PyThreadState"}:
                paths = ["Include/internal/pycore_tstate.h"]
            elif typ == "PyGenObject":
                paths = ["Include/cpython/genobject.h", "Objects/genobject.c"]
            entry = entries.setdefault(
                key,
                SymbolEntry(symbol=typ, kind="struct_type", cpython_paths=paths),
            )
            entry.add_site(rel, line_no)

        for match in _MIRROR_COMMENT_RE.finditer(line):
            hint: str = match.group("hint")
            key = f"mirror:{hint}"
            entry = entries.setdefault(
                key,
                SymbolEntry(
                    symbol=hint,
                    kind="c_file_mirror",
                    cpython_paths=_mirror_paths_for_hint(hint),
                    notes="comment/name mirror of CPython .c layout",
                ),
            )
            entry.add_site(rel, line_no)

        for match in _STACKREF_API_RE.finditer(line):
            sym = match.group("sym")
            key = f"stackref:{sym}"
            entry = entries.setdefault(
                key,
                SymbolEntry(
                    symbol=sym,
                    kind="stackref_api",
                    cpython_paths=["Include/internal/pycore_stackref.h"],
                ),
            )
            entry.add_site(rel, line_no)

        for match in _DEBUG_SYM_RE.finditer(line):
            sym = match.group("sym")
            key = f"debug_sym:{sym}"
            entry = entries.setdefault(
                key,
                SymbolEntry(
                    symbol=sym,
                    kind="debug_symbol",
                    cpython_paths=["Modules/_asynciomodule.c"],
                ),
            )
            entry.add_site(rel, line_no)

        if is_python:
            for match in _PY_VERSION_INFO_RE.finditer(line):
                sym = f"sys.version_info {match.group('op')} ({match.group('maj')}, {match.group('min')})"
                key = f"py_version_guard:{sym}"
                entry = entries.setdefault(
                    key,
                    SymbolEntry(symbol=sym, kind="version_guard"),
                )
                entry.add_site(rel, line_no)

            if _SYS_MONITORING_RE.search(line):
                key = "python_api:sys.monitoring"
                entry = entries.setdefault(
                    key,
                    SymbolEntry(
                        symbol="sys.monitoring",
                        kind="python_api",
                        cpython_paths=["Python/instrumentation.c", "Lib/asyncio"],
                        notes="asyncio hook path on 3.15+",
                    ),
                )
                entry.add_site(rel, line_no)

            if _ASYNCIO_PRIVATE_RE.search(line):
                key = "python_api:asyncio_internals"
                entry = entries.setdefault(
                    key,
                    SymbolEntry(
                        symbol="asyncio private / task APIs",
                        kind="python_api",
                        cpython_paths=["Lib/asyncio", "Modules/_asynciomodule.c"],
                    ),
                )
                entry.add_site(rel, line_no)

            if re.search(r"""\bwrap\b""", line) and "asyncio" in text:
                key = "python_api:wrapping.wrap"
                entry = entries.setdefault(
                    key,
                    SymbolEntry(
                        symbol="ddtrace.internal.wrapping.wrap",
                        kind="python_api",
                        cpython_paths=["Lib/asyncio"],
                        notes="bytecode patching; may break when asyncio uses builtins",
                    ),
                )
                entry.add_site(rel, line_no)


def build_inventory(repo_root: Path | None = None) -> dict[str, object]:
    """Scan profiling trees and return a serializable inventory document."""
    root: Path = repo_root if repo_root is not None else REPO_ROOT
    scan_roots: list[Path] = [root / rel for rel in INVENTORY_SCAN_ROOTS]
    # version_compat.h lives under profiling_helpers inside the first root already.
    entries: dict[str, SymbolEntry] = {}
    files_scanned: int = 0
    for path in _iter_source_files(scan_roots):
        files_scanned += 1
        scan_file(path, root, entries)

    # Sort sites for stable JSON.
    symbols_out: list[dict[str, object]] = []
    for key in sorted(entries):
        entry: SymbolEntry = entries[key]
        entry.sites.sort(key=lambda s: (s.file, s.line))
        # Deduplicate cpython_paths while preserving order.
        seen_paths: set[str] = set()
        paths_unique: list[str] = []
        for p in entry.cpython_paths:
            if p not in seen_paths:
                seen_paths.add(p)
                paths_unique.append(p)
        entry.cpython_paths = paths_unique
        symbols_out.append(
            {
                "key": key,
                "symbol": entry.symbol,
                "kind": entry.kind,
                "sites": [asdict(s) for s in entry.sites],
                "cpython_paths": entry.cpython_paths,
                "notes": entry.notes,
            }
        )

    # Derived path set for diff.py.
    path_set: set[str] = set()
    for item in symbols_out:
        raw_paths: object = item["cpython_paths"]
        if not isinstance(raw_paths, list):
            continue
        for p in raw_paths:
            if isinstance(p, str):
                path_set.add(p)

    by_kind: dict[str, int] = defaultdict(int)
    for item in symbols_out:
        kind: str = str(item["kind"])
        by_kind[kind] += 1

    return {
        "schema_version": 1,
        "scan_roots": list(INVENTORY_SCAN_ROOTS),
        "files_scanned": files_scanned,
        "symbol_count": len(symbols_out),
        "by_kind": dict(sorted(by_kind.items())),
        "cpython_paths": sorted(path_set),
        "symbols": symbols_out,
    }


def default_inventory_path(repo_root: Path | None = None) -> Path:
    root: Path = repo_root if repo_root is not None else REPO_ROOT
    return root / "scripts" / "cpython_delta" / "inventory.json"


def write_inventory(doc: dict[str, object], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    out_stream: TextIO = stdout if stdout is not None else sys.stdout
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="dd-trace-py repository root",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output JSON path (default: scripts/cpython_delta/inventory.json)",
    )
    args: argparse.Namespace = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    out_path: Path = args.out if args.out is not None else default_inventory_path(repo_root)
    doc: dict[str, object] = build_inventory(repo_root)
    write_inventory(doc, out_path)

    out_stream.write(
        f"Wrote {out_path} ({doc['symbol_count']} symbols, "
        f"{doc['files_scanned']} files, {len(doc['cpython_paths'])} cpython paths)\n"  # type: ignore[arg-type]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
