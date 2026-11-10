"""Compatibility entry points for product-owned integration adapters."""

from ddtrace.llmobs._subscribers import integration_for as get_integration
from ddtrace.llmobs._subscribers import register as register
from ddtrace.llmobs._subscribers import unregister as unregister


__all__ = ["get_integration", "register", "unregister"]
