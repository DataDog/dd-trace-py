"""Service registry to avoid circular imports in CI Visibility system."""

import typing as t


# CIVisibility is typed as t.Any to avoid a circular import with recorder.py.
# The recorder registers a CIVisibility instance at runtime via
# register_ci_visibility_instance(); callers access it through
# require_ci_visibility_service().
CI_VISIBILITY_INSTANCE: t.Any = None


def register_ci_visibility_instance(service: t.Any) -> None:
    """Register the CIVisibility service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = service


def unregister_ci_visibility_instance() -> None:
    """Unregister the current service instance."""
    global CI_VISIBILITY_INSTANCE
    CI_VISIBILITY_INSTANCE = None


def require_ci_visibility_service() -> t.Any:
    """Get the CIVisibility service, raising if not available."""
    if not CI_VISIBILITY_INSTANCE:
        raise RuntimeError("CIVisibility service not registered")
    return CI_VISIBILITY_INSTANCE
