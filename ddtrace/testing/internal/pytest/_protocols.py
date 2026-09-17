"""Protocols for testing.internal.pytest types, to break circular imports.

These protocols allow modules to depend on the interface rather than the
concrete implementation, avoiding circular imports between pytest plugin modules.
"""

import typing as t

from ddtrace.testing.internal.session_manager import SessionManager


class TestOptPluginProtocol(t.Protocol):
    """Protocol for TestOptPlugin, to avoid a circular import between bdd.py and plugin.py."""

    manager: SessionManager
