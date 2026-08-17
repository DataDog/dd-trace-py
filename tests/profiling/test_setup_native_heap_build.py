"""Unit tests for setup.py native heap-gotter build opt-in."""

from __future__ import annotations

import os
from pathlib import Path
import sysconfig
from typing import Any
from typing import cast
from unittest import mock

import pytest


_SETUP_PATH = Path(__file__).resolve().parents[2] / "setup.py"
# setup.py lines 134-164: _env_truthy, is_musl_libc, musl warning, BUILD_NATIVE_HEAP_GOTTER.
_BUILD_LOGIC_START = 133
_BUILD_LOGIC_END = 164


def _exec_build_logic(
    *,
    sysconfig_values: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Exec the native-heap build gating block from setup.py in isolation."""
    source = _SETUP_PATH.read_text()
    lines = source.splitlines(keepends=True)
    code = "".join(lines[_BUILD_LOGIC_START:_BUILD_LOGIC_END])

    namespace: dict[str, Any] = {"os": os, "sysconfig": sysconfig}

    if sysconfig_values is not None:
        original_get = sysconfig.get_config_var

        def fake_get_config_var(name: str) -> str | None:
            if name in sysconfig_values:
                return sysconfig_values[name]
            return cast(str | None, original_get(name))

        namespace["sysconfig"] = mock.Mock()
        namespace["sysconfig"].get_config_var = fake_get_config_var

    captured: list[str] = []

    def capture_print(*args: object, **kwargs: object) -> None:
        captured.append(" ".join(str(arg) for arg in args))

    with mock.patch("builtins.print", capture_print):
        exec(code, namespace)  # noqa: S102

    namespace["_stdout"] = "\n".join(captured)
    return namespace


class TestEnvTruthy:
    @pytest.mark.parametrize("value", ("1", "yes", "on", "true", "TRUE", "Yes"))
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("DD_TEST_ENV_TRUTHY", value)
        ns = _exec_build_logic()
        assert ns["_env_truthy"]("DD_TEST_ENV_TRUTHY") is True

    @pytest.mark.parametrize("value", ("0", "false", "no", "maybe"))
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("DD_TEST_ENV_TRUTHY", value)
        ns = _exec_build_logic()
        assert ns["_env_truthy"]("DD_TEST_ENV_TRUTHY") is False

    def test_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_TEST_ENV_TRUTHY", raising=False)
        ns = _exec_build_logic()
        assert ns["_env_truthy"]("DD_TEST_ENV_TRUTHY") is False


class TestIsMuslLibc:
    def test_detects_musl_from_soabi(self) -> None:
        ns = _exec_build_logic(
            sysconfig_values={"SOABI": "cpython-311-x86_64-linux-musl"},
        )
        assert ns["is_musl_libc"]() is True

    def test_detects_musl_from_ext_suffix(self) -> None:
        ns = _exec_build_logic(
            sysconfig_values={"EXT_SUFFIX": ".cpython-311-x86_64-linux-musl.so"},
        )
        assert ns["is_musl_libc"]() is True

    def test_glibc_not_musl(self) -> None:
        ns = _exec_build_logic(
            sysconfig_values={
                "SOABI": "cpython-311-x86_64-linux-gnu",
                "EXT_SUFFIX": ".cpython-311-x86_64-linux-gnu.so",
                "BUILD_GNU_TYPE": "x86_64-pc-linux-gnu",
            },
        )
        assert ns["is_musl_libc"]() is False


class TestBuildNativeHeapGotter:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_NATIVE_HEAP_ENABLED", raising=False)
        ns = _exec_build_logic()
        assert ns["BUILD_NATIVE_HEAP_GOTTER"] is False

    def test_enabled_when_env_set_on_glibc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "1")
        ns = _exec_build_logic(
            sysconfig_values={"SOABI": "cpython-311-x86_64-linux-gnu"},
        )
        assert ns["BUILD_NATIVE_HEAP_GOTTER"] is True

    def test_disabled_on_musl_even_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "true")
        ns = _exec_build_logic(
            sysconfig_values={"SOABI": "cpython-311-x86_64-linux-musl"},
        )
        assert ns["BUILD_NATIVE_HEAP_GOTTER"] is False

    def test_warns_on_musl_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "1")
        ns = _exec_build_logic(
            sysconfig_values={"SOABI": "cpython-311-x86_64-linux-musl"},
        )
        assert "WARNING" in ns["_stdout"]
        assert "musllinux" in ns["_stdout"]
