"""Protocols for ci_visibility.api types, to break circular imports.

These protocols allow modules to depend on the interface rather than the
concrete implementation, avoiding circular imports between api modules.
"""

import typing as t
from typing import Any


class TestVisibilitySessionProtocol(t.Protocol):
    """Protocol for the session object returned by get_session().

    Defined here so that _base.py can use it as a return type without
    importing from _session.py (which would create a circular import).
    """

    def get_child_by_id(self, child_id: Any) -> Any: ...

    def get_session_settings(self) -> Any: ...

    def efd_is_faulty_session(self) -> bool: ...

    def atr_max_retries_reached(self) -> bool: ...

    def _atr_count_retry(self) -> None: ...
