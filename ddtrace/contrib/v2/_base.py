"""
Base instrumentation plugin class.

InstrumentationPlugin is the base class for all integration patches.
It handles configuration, version checking, and patch lifecycle.
"""

from abc import ABC
from abc import abstractmethod
from importlib.metadata import version as get_pkg_version
from typing import Any
from typing import Dict
from typing import Optional

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from ddtrace import config as ddtrace_config


class InstrumentationPlugin(ABC):
    """
    Base class for instrumentation plugins (producers).

    This class is responsible for:
    - Configuration management
    - Version checking and compatibility
    - Applying/removing patches from target libraries
    - Providing trace helpers to submit data via event structs

    Subclasses implement patch() and unpatch() with library-specific logic.
    """

    name: str = ""  # Integration name (e.g., "asyncpg", "requests")
    supported_versions: str = ""  # Version constraint (e.g., ">=0.22.0")

    def __init__(self):
        self._patched = False
        self._config: Dict[str, Any] = {}  # Per-integration configuration

    def configure(self, config: Dict[str, Any]) -> None:
        """
        Configure the plugin with integration-specific settings.

        Called by the plugin loader during initialization.
        """
        self._config = config

    @property
    def integration_config(self) -> Any:
        """Get config from ddtrace.config for this integration."""
        return getattr(ddtrace_config, self.name, {})

    @abstractmethod
    def patch(self) -> None:
        """
        Apply patches to the target library.

        Implementation should:
        1. Check if library is installed
        2. Verify version compatibility
        3. Apply wrapt wrappers to target methods
        4. Set self._patched = True
        """
        pass

    @abstractmethod
    def unpatch(self) -> None:
        """
        Remove patches from the target library.

        Implementation should:
        1. Restore original methods
        2. Set self._patched = False
        """
        pass

    def get_version(self) -> Optional[str]:
        """Get the installed version of the target library."""
        try:
            return get_pkg_version(self.name)
        except Exception:
            return None

    def get_versions(self) -> Dict[str, Optional[str]]:
        """
        Return versions for the instrumented library and dependencies.

        Override to include additional dependency versions.
        """
        return {self.name: self.get_version()}

    def is_supported(self) -> bool:
        """Check if the installed library version is supported."""
        if not self.supported_versions:
            return True

        version = self.get_version()
        if not version:
            return False

        try:
            return Version(version) in SpecifierSet(self.supported_versions)
        except Exception:
            return True

    @property
    def is_patched(self) -> bool:
        """Check if patches are currently applied."""
        return self._patched
