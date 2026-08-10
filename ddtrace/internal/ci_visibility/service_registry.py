"""Service registry to avoid circular imports in CI Visibility system."""

import typing as t

from ddtrace.internal.ci_visibility._protocols import CIVisibilityProtocol


CI_VISIBILITY_INSTANCE: t.Optional[CIVisibilityProtocol] = None


def register_ci_visibility_instance(service: CIVisibilityProtocol) -> None:
    """Register the CIVisibility service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = service


def unregister_ci_visibility_instance() -> None:
    """Unregister the current service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = None


def require_ci_visibility_service() -> CIVisibilityProtocol:
    """Get the CIVisibility service, raising if not available."""
    if not CI_VISIBILITY_INSTANCE:
        raise RuntimeError("CIVisibility service not registered")
    return CI_VISIBILITY_INSTANCE
