"""
Unit tests for the native AgentResponse PyO3 class.

Verifies that construction accepts plain dicts, any Mapping-like object, and
edge-case inputs, and that the resulting rate_by_service attribute is always
a plain Python dict.
"""

from collections.abc import Mapping
from types import MappingProxyType

import pytest

from ddtrace.internal.native._native import AgentResponse


class CustomMapping(Mapping):
    """Minimal Mapping subclass without dict inheritance."""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)


class TestAgentResponseConstruction:
    def test_plain_dict(self):
        rates = {"service:foo,env:prod": 0.5, "service:,env:": 1.0}
        resp = AgentResponse(rates)
        assert resp.rate_by_service == rates
        assert isinstance(resp.rate_by_service, dict)

    def test_mapping_proxy(self):
        rates = {"service:foo,env:": 0.25}
        resp = AgentResponse(MappingProxyType(rates))
        assert resp.rate_by_service == rates
        assert isinstance(resp.rate_by_service, dict)

    def test_custom_mapping(self):
        rates = {"service:bar,env:staging": 0.1}
        resp = AgentResponse(CustomMapping(rates))
        assert resp.rate_by_service == rates
        assert isinstance(resp.rate_by_service, dict)

    def test_empty_dict(self):
        resp = AgentResponse({})
        assert resp.rate_by_service == {}
        assert isinstance(resp.rate_by_service, dict)

    def test_default_key(self):
        resp = AgentResponse({"service:,env:": 1.0})
        assert resp.rate_by_service["service:,env:"] == 1.0

    def test_rate_by_service_is_dict_not_original_object(self):
        # Mutations to the original mapping must not affect the stored value.
        original = {"service:,env:": 0.5}
        resp = AgentResponse(original)
        original["service:,env:"] = 0.9
        assert resp.rate_by_service["service:,env:"] == 0.5

    def test_non_mapping_raises(self):
        with pytest.raises((TypeError, ValueError)):
            AgentResponse("not a mapping")  # type: ignore[arg-type]

    def test_non_mapping_list_raises(self):
        with pytest.raises((TypeError, ValueError)):
            AgentResponse([("service:,env:", 1.0)])  # type: ignore[arg-type]
