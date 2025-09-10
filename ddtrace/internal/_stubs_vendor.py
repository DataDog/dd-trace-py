"""
Stubs for vendor modules and other dependencies.
"""

from ._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    from ddtrace.internal.compat import Path
    from ddtrace.internal.logger import get_logger
    from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
    from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
    from ddtrace.vendor.debtcollector import deprecate
    from ddtrace.vendor.packaging.specifiers import SpecifierSet
    from ddtrace.vendor.packaging.version import Version

    from . import telemetry

    def load_common_appsec_modules():
        # Import here to avoid circular imports
        from ddtrace.appsec._listeners import load_common_appsec_modules as _load_common_appsec_modules
        return _load_common_appsec_modules()
else:
    # Path stub for file operations
    class Path:  # type: ignore[no-redef]
        def __init__(self, path):
            self.path = path

        def __truediv__(self, other):
            return Path(f"{self.path}/{other}")

        def exists(self):
            return False

        @property
        def parent(self):
            return Path("/")

    # Telemetry namespace stub
    class TELEMETRY_NAMESPACE:  # type: ignore[no-redef]
        TRACERS = "tracers"

    # Deprecation function stub
    def deprecate(  # type: ignore[misc]
        prefix=None, postfix=None, message=None, version=None, removal_version=None, stacklevel=None, category=None
    ):
        pass

    # Version handling stubs
    class SpecifierSet:  # type: ignore[no-redef]
        def __init__(self, spec):
            pass

        def __contains__(self, version):
            return True

    class Version:  # type: ignore[no-redef]
        def __init__(self, version):
            pass

    # Telemetry stub
    class telemetry:  # type: ignore[no-redef]
        class telemetry_writer:
            @staticmethod
            def add_integration(*args, **kwargs):
                pass

            @staticmethod
            def add_count_metric(*args, **kwargs):
                pass

    # Logger stub
    def get_logger(name):  # type: ignore[misc]
        class MockLogger:
            def info(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        return MockLogger()

    # Deprecation warning stub
    class DDTraceDeprecationWarning:  # type: ignore[no-redef]
        pass

    # Helper function stubs
    def load_common_appsec_modules():
        pass


# Export the vendor stubs
__all__ = [
    "Path",
    "TELEMETRY_NAMESPACE",
    "deprecate",
    "SpecifierSet",
    "Version",
    "telemetry",
    "get_logger",
    "DDTraceDeprecationWarning",
    "load_common_appsec_modules"
]
