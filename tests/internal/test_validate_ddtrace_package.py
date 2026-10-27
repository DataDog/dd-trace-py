"""Regression checks for .gitlab/validate-ddtrace-package.py expected platforms."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


_VALIDATOR = Path(__file__).resolve().parents[2] / ".gitlab" / "validate-ddtrace-package.py"


@pytest.fixture(scope="module")
def validator() -> ModuleType:
    spec: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_file_location(
        "_validate_ddtrace_package_under_test", _VALIDATOR
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cp315_requires_linux_and_macos_not_windows(validator: ModuleType) -> None:
    required: list[str] = validator.REQUIRED_PLATFORMS["cp315"]
    assert any("linux" in p for p in required)
    assert "macosx_14_0_arm64" in required
    assert "macosx_14_0_x86_64" in required
    assert "win32" not in required
    assert "win_amd64" not in required

    filtered: list[str] = validator.required_platforms("cp315", validator.BASE_PLATFORMS)
    assert filtered == required
    assert "win32" not in filtered
    assert "win_amd64" not in filtered
