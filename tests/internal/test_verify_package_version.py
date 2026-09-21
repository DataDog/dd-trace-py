"""Tests for scripts/verify-package-version."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "verify-package-version"


@pytest.fixture(scope="module")
def verify_mod() -> ModuleType:
    # The script has no .py suffix, so spec_from_file_location returns None.
    loader: importlib.machinery.SourceFileLoader = importlib.machinery.SourceFileLoader(
        "verify_package_version", str(_SCRIPT_PATH)
    )
    spec: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("version", "ok", "canonical"),
    [
        ("4.16.0rc1", True, "4.16.0rc1"),
        ("4.16.0rc1+identify.foreign.segv", True, "4.16.0rc1+identify.foreign.segv"),
        ("4.16.0rc1+identify-foreign-segv", True, "4.16.0rc1+identify.foreign.segv"),
        ("4.16.0rc1+identify_foreign_segv", True, "4.16.0rc1+identify.foreign.segv"),
        ("4.1.0.dev0", True, "4.1.0.dev0"),
        ("4.1.0.dev", False, "4.1.0.dev0"),
        ("not-a-version", False, ""),
        ("1.0+", False, ""),
    ],
)
def test_is_pep440_compliant(verify_mod: ModuleType, version: str, ok: bool, canonical: str) -> None:
    result: tuple[bool, str] = verify_mod.is_pep440_compliant(version)
    is_compliant: bool
    normalized: str
    is_compliant, normalized = result
    assert is_compliant is ok
    assert normalized == canonical
