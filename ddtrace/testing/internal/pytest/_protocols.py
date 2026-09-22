"""Protocols for testing.internal.pytest types, to break circular imports.

These protocols allow modules to depend on the interface rather than the
concrete implementation, avoiding circular imports between pytest plugin modules.
"""

from pathlib import Path
import typing as t

from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.test_data import TestSession


class TestOptPluginProtocol(t.Protocol):
    """Protocol for TestOptPlugin, to avoid a circular import between bdd.py and plugin.py."""

    manager: SessionManager
    session: TestSession
    xdist_atr_crash_state_path: t.Optional[Path]
    is_xdist_worker: bool
