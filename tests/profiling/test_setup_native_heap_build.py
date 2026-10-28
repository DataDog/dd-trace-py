"""Unit tests for setup.py native heap-gotter build opt-in."""

from __future__ import annotations

import os
from pathlib import Path
import sysconfig
from typing import Any
from typing import Optional
from typing import cast
from unittest import mock

import pytest


_SETUP_PATH = Path(__file__).resolve().parents[2] / "setup.py"


def _build_logic_source() -> str:
    """Slice setup.py from `_env_truthy` through the `BUILD_NATIVE_HEAP_GOTTER` assignment."""
    source = _SETUP_PATH.read_text()
    start = source.index("def _env_truthy(")
    assign = source.index("BUILD_NATIVE_HEAP_GOTTER:")
    return source[start : source.index("\n", assign)]


def _exec_build_logic(
    *,
    sysconfig_values: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Exec the native-heap build gating block from setup.py in isolation."""
    code = _build_logic_source()

    namespace: dict[str, Any] = {"os": os, "sysconfig": sysconfig}

    if sysconfig_values is not None:
        original_get = sysconfig.get_config_var

        def fake_get_config_var(name: str) -> Optional[str]:
            if name in sysconfig_values:
                return sysconfig_values[name]
            # cast() evaluates its type expr at runtime; str | None is 3.10+.
            return cast(Optional[str], original_get(name))

        namespace["sysconfig"] = mock.Mock()
        namespace["sysconfig"].get_config_var = fake_get_config_var

    exec(code, namespace)  # noqa: S102
    return namespace


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DD_PROFILING_NATIVE_HEAP_ENABLED", raising=False)
    ns = _exec_build_logic()
    assert ns["BUILD_NATIVE_HEAP_GOTTER"] is False


def test_enabled_when_env_set_on_glibc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "1")
    ns = _exec_build_logic(
        sysconfig_values={"SOABI": "cpython-311-x86_64-linux-gnu"},
    )
    assert ns["BUILD_NATIVE_HEAP_GOTTER"] is True


def test_disabled_on_musl_even_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "true")
    ns = _exec_build_logic(
        sysconfig_values={"SOABI": "cpython-311-x86_64-linux-musl"},
    )
    assert ns["BUILD_NATIVE_HEAP_GOTTER"] is False
