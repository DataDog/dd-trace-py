"""Service registry to avoid circular imports in CI Visibility system."""

import typing as t


class _CIVisibilityProtocol(t.Protocol):
    """Protocol for CIVisibility, to avoid a circular import with recorder.py.

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


CI_VISIBILITY_INSTANCE: t.Optional[_CIVisibilityProtocol] = None


def register_ci_visibility_instance(service: _CIVisibilityProtocol) -> None:
    """Register the CIVisibility service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = service


def unregister_ci_visibility_instance() -> None:
    """Unregister the current service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = None


def require_ci_visibility_service() -> _CIVisibilityProtocol:
    """Get the CIVisibility service, raising if not available."""
    if not CI_VISIBILITY_INSTANCE:
        raise RuntimeError("CIVisibility service not registered")
    return CI_VISIBILITY_INSTANCE
