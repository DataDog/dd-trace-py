"""Service registry to avoid circular imports in CI Visibility system."""
import typing as t

if t.TYPE_CHECKING:
    from ddtrace.internal.ci_visibility.recorder import CIVisibility


class CIVisibilityServiceRegistry:
    """Registry to access CIVisibility instance without circular imports.

    Since CIVisibility is a singleton, no locks are needed.
    """

    _instance: t.Optional["CIVisibility"] = None

    @classmethod
    def register(cls, service: "CIVisibility") -> None:
        """Register the CIVisibility service instance."""
        cls._instance = service

    @classmethod
    def unregister(cls) -> None:
        """Unregister the current service instance."""
        cls._instance = None

    @classmethod
    def get_service(cls) -> t.Optional["CIVisibility"]:
        """Get the registered CIVisibility service instance."""
        return cls._instance

    @classmethod
    def require_service(cls) -> "CIVisibility":
        """Get the registered service, raising if not available."""
        service = cls.get_service()
        if service is None:
            raise RuntimeError("CIVisibility service not registered")
        return service


# Convenience functions
def get_ci_visibility_service() -> t.Optional["CIVisibility"]:
    """Get the CIVisibility service if available."""
    return CIVisibilityServiceRegistry.get_service()


def require_ci_visibility_service() -> "CIVisibility":
    """Get the CIVisibility service, raising if not available."""
    return CIVisibilityServiceRegistry.require_service()
