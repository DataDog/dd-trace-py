"""Unit tests for OpenAI HTTP client module selection (httpx vs httpx2)."""

import sys
from types import ModuleType
from types import SimpleNamespace

import httpx
import pytest

from tests.aiguard.openai import _http_client as http_client
from tests.aiguard.openai._http_client import _http_client_module


def test_http_client_module_uses_httpx2_when_openai_is_3(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_httpx2: ModuleType = ModuleType("httpx2")
    monkeypatch.setattr(http_client, "httpx2_mod", fake_httpx2)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="3.0.0"))

    chosen: ModuleType = _http_client_module()
    assert chosen is fake_httpx2


def test_http_client_module_uses_httpx_when_openai_is_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="1.66.0"))

    chosen: ModuleType = _http_client_module()
    assert chosen is httpx


def test_http_client_module_requires_httpx2_when_openai_is_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_client, "httpx2_mod", None)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(__version__="3.1.0"))

    with pytest.raises(ImportError, match="httpx2"):
        _http_client_module()
