"""Service registry to avoid circular imports in CI Visibility system."""

import typing as t

from ddtrace.internal.codeowners import Codeowners
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.trace import Tracer


class CIVisibilityServiceProtocol(t.Protocol):
    """Structural interface for the CIVisibility service singleton.

    This mirrors the subset of the public surface of ddtrace.internal.ci_visibility.recorder.CIVisibility that is
    used via require_ci_visibility_service(). It exists so that this module does not need to import
    ddtrace.internal.ci_visibility.recorder, which itself imports from this module.
    """

    enabled: bool

    def get_item_by_id(self, item_id: t.Any) -> t.Any: ...

    def get_session(self) -> t.Any: ...

    def get_module_by_id(self, module_id: t.Any) -> t.Any: ...

    def get_suite_by_id(self, suite_id: t.Any) -> t.Any: ...

    def get_test_by_id(self, test_id: t.Any) -> t.Any: ...

    def get_codeowners(self) -> t.Optional[Codeowners]: ...

    def get_tracer(self) -> t.Optional[Tracer]: ...

    def get_workspace_path(self) -> t.Optional[str]: ...

    def should_collect_coverage(self) -> bool: ...

    def test_skipping_enabled(self) -> bool: ...

    def is_atr_enabled(self) -> bool: ...

    def set_library_capabilities(self, capabilities: LibraryCapabilities) -> None: ...


CI_VISIBILITY_INSTANCE: t.Optional[CIVisibilityServiceProtocol] = None


def register_ci_visibility_instance(service: CIVisibilityServiceProtocol) -> None:
    """Register the CIVisibility service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = service


def unregister_ci_visibility_instance() -> None:
    """Unregister the current service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = None


def require_ci_visibility_service() -> CIVisibilityServiceProtocol:
    """Get the CIVisibility service, raising if not available."""
    if not CI_VISIBILITY_INSTANCE:
        raise RuntimeError("CIVisibility service not registered")
    return CI_VISIBILITY_INSTANCE
