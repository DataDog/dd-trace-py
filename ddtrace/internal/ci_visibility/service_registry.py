"""Service registry to avoid circular imports in CI Visibility system."""
import typing as t


if t.TYPE_CHECKING:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

CI_VISIBILITY_INSTANCE = None


def register_ci_visibility_instance(service: "CIVisibility") -> None:
    """Register the CIVisibility service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = service


def unregister_ci_visibility_instance() -> None:
    """Unregister the current service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = None


def require_ci_visibility_service() -> "CIVisibility":
    """Get the CIVisibility service, raising if not available."""
    if not CI_VISIBILITY_INSTANCE:
        raise RuntimeError("CIVisibility service not registered")
    return CI_VISIBILITY_INSTANCE
