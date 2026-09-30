"""Shared paths and constants for the cpython_delta pipeline."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_CPYTHON_ROOT: Path = Path.home() / "dd" / "cpython"

INVENTORY_SCAN_ROOTS: tuple[str, ...] = (
    "ddtrace/internal/datadog/profiling",
    "ddtrace/profiling",
)

FIXED_WATCH_PATHS: tuple[str, ...] = (
    "Include/internal/pycore_frame.h",
    "Include/internal/pycore_interpframe.h",
    "Include/internal/pycore_interpframe_structs.h",
    "Include/internal/pycore_code.h",
    "Include/internal/pycore_tstate.h",
    "Include/internal/pycore_interp.h",
    "Include/internal/pycore_debug_offsets.h",
    "Include/internal/pycore_stackref.h",
    "Include/internal/pycore_pystate.h",
    "Include/internal/pycore_runtime.h",
    "Include/internal/pycore_llist.h",
    "Include/cpython/genobject.h",
    "Objects/genobject.c",
    "Objects/frameobject.c",
    "Modules/_asynciomodule.c",
    "Modules/_remote_debugging",
    "Lib/asyncio",
    "Python/instrumentation.c",
)

SOURCE_SUFFIXES: frozenset[str] = frozenset({".c", ".cpp", ".cc", ".h", ".hpp", ".pyx", ".pxd", ".py"})
