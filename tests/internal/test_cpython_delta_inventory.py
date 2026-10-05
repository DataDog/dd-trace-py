"""Scanner pins for layout-sensitive member accesses and setup.py scan roots."""

from __future__ import annotations

from importlib.machinery import ModuleSpec
import importlib.util
import pathlib
import sys
from typing import Any

import pytest


_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_INVENTORY_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "cpython_delta" / "inventory.py"
_COMMON_SCRIPT: pathlib.Path = _REPO_ROOT / "scripts" / "cpython_delta" / "common.py"

# Active CPython layout fields used in stack/echion + profiling_helpers.
_EXPECTED_LAYOUT_FIELDS: tuple[str, ...] = (
    "co_filename",
    "co_linetable",
    "co_firstlineno",
    "co_name",
    "co_qualname",
    "instr_ptr",
    "prev_instr",
    "f_code",
    "f_back",
    "f_lasti",
)


@pytest.fixture(scope="module")
def inventory_mod() -> Any:
    # common.py must be importable as ``common`` when inventory.py loads.
    common_spec: ModuleSpec | None = importlib.util.spec_from_file_location("common", _COMMON_SCRIPT)
    assert common_spec is not None and common_spec.loader is not None
    common_module: Any = importlib.util.module_from_spec(common_spec)
    sys.modules["common"] = common_module
    common_spec.loader.exec_module(common_module)

    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "cpython_delta_inventory_scan",
        _INVENTORY_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_layout_fields_cover_active_frame_and_code_members(inventory_mod: Any) -> None:
    layout: frozenset[str] = inventory_mod._LAYOUT_FIELDS
    for field in _EXPECTED_LAYOUT_FIELDS:
        assert field in layout, field


def test_scan_file_records_layout_member_accesses(
    inventory_mod: Any,
    tmp_path: pathlib.Path,
) -> None:
    """Member accesses like ``code->co_filename`` must become inventory tokens."""
    src: pathlib.Path = tmp_path / "frame_sample.cc"
    body: str = "\n".join(
        [
            "void sample(PyCodeObject* code, _PyInterpreterFrame* frame) {",
            "  auto* name = code->co_name;",
            "  auto* qname = code->co_qualname;",
            "  auto* file = code->co_filename;",
            "  auto* table = code->co_linetable;",
            "  int line = code->co_firstlineno;",
            "  auto* ip = frame->instr_ptr;",
            "  auto* prev = frame->prev_instr;",
            "  auto* fcode = frame->f_code;",
            "  auto* back = frame->f_back;",
            "  int lasti = frame->f_lasti;",
            "  (void)name; (void)qname; (void)file; (void)table; (void)line;",
            "  (void)ip; (void)prev; (void)fcode; (void)back; (void)lasti;",
            "}",
            "",
        ]
    )
    src.write_text(body, encoding="utf-8")
    entries: dict[str, Any] = {}
    inventory_mod.scan_file(src, tmp_path, entries)
    for field in _EXPECTED_LAYOUT_FIELDS:
        key: str = f"field_access:{field}"
        assert key in entries, key
        assert entries[key].symbol == field
        assert entries[key].sites


def test_build_inventory_scans_setup_py_version_guards(
    inventory_mod: Any,
    tmp_path: pathlib.Path,
) -> None:
    """setup.py profiling gates must appear as version_guard inventory rows."""
    setup_py: pathlib.Path = tmp_path / "setup.py"
    setup_py.write_text(
        "import sys\nif sys.version_info < (3, 16):\n    rust_features = ['profiling']\n",
        encoding="utf-8",
    )
    # Minimal trees so build_inventory only hits our stub setup.py via roots.
    (tmp_path / "ddtrace" / "internal" / "datadog" / "profiling").mkdir(parents=True)
    (tmp_path / "ddtrace" / "profiling").mkdir(parents=True)

    # inventory.py binds INVENTORY_SCAN_ROOTS at import; patch that binding.
    original_roots: tuple[str, ...] = inventory_mod.INVENTORY_SCAN_ROOTS
    inventory_mod.INVENTORY_SCAN_ROOTS = (
        "ddtrace/internal/datadog/profiling",
        "ddtrace/profiling",
        "setup.py",
    )
    try:
        doc: dict[str, Any] = inventory_mod.build_inventory(tmp_path)
    finally:
        inventory_mod.INVENTORY_SCAN_ROOTS = original_roots

    assert "setup.py" in doc["scan_roots"]
    symbols: list[dict[str, Any]] = list(doc["symbols"])
    guard_keys: list[str] = [str(row["key"]) for row in symbols if str(row["kind"]) == "version_guard"]
    assert any("sys.version_info" in key and "3, 16" in key for key in guard_keys)
    setup_sites: list[dict[str, Any]] = []
    for row in symbols:
        if row.get("kind") != "version_guard":
            continue
        for site in row.get("sites", []):
            if site.get("file") == "setup.py":
                setup_sites.append(site)
    assert setup_sites
