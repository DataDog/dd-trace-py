"""Unit tests for OpenAI HTTP client module selection (httpx vs httpx2)."""

import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

import tests.aiguard.openai._http_client as http_client


def test_http_client_helper_imports_without_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI 3 CI may ship only ``httpx2``; helper import must not require ``httpx``."""
    monkeypatch.setitem(sys.modules, "httpx", None)

    reloaded: ModuleType = importlib.reload(http_client)

    assert reloaded._http_client_module is not None


def test_http_client_module_uses_httpx2_when_openai_is_3(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_httpx2: ModuleType = ModuleType("httpx2")
    monkeypatch.setitem(sys.modules, "httpx2", fake_httpx2)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="3.0.0"))

    chosen: ModuleType = http_client._http_client_module()
    assert chosen is fake_httpx2


def test_http_client_module_uses_httpx_when_openai_is_1(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_httpx: ModuleType = ModuleType("httpx")
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="1.66.0"))

    chosen: ModuleType = http_client._http_client_module()
    assert chosen is fake_httpx


def test_http_client_module_requires_httpx2_when_openai_is_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "httpx2", None)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="3.1.0"))

    with pytest.raises(ImportError, match="httpx2"):
        http_client._http_client_module()
