"""Protocols for ci_visibility types, to break circular imports.

These protocols allow modules to depend on the interface rather than the
concrete implementation, avoiding circular imports between ci_visibility modules.
"""

import typing as t


class CIVisibilityProtocol(t.Protocol):
    """Protocol for CIVisibility, to avoid a circular import between service_registry.py and recorder.py.

    Return types for methods that return ci_visibility.api.* types use t.Any
    to avoid importing those modules (which would create new cycles).
    """

    enabled: bool

    def test_skipping_enabled(self) -> bool: ...

    def is_atr_enabled(self) -> bool: ...

    def should_collect_coverage(self) -> bool: ...

    def get_item_by_id(self, item_id: t.Any) -> t.Any: ...

    def get_session(self) -> t.Any: ...

    def get_module_by_id(self, item_id: t.Any) -> t.Any: ...

    def get_suite_by_id(self, item_id: t.Any) -> t.Any: ...

    def get_test_by_id(self, item_id: t.Any) -> t.Any: ...

    def get_tracer(self) -> t.Any: ...

    def get_codeowners(self) -> t.Any: ...

    def get_workspace_path(self) -> t.Optional[str]: ...

    def set_library_capabilities(self, capabilities: t.Any) -> None: ...
