import importlib
import sys
from types import ModuleType
from typing import Optional, Any, Dict, Iterator
import os
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec

from ddtrace.internal.logger import get_logger
import pdb


log = get_logger(__name__)


class DummyCallable:
    """A dummy callable that returns None when called and handles inspection."""

    def __init__(self, module_name: str):
        self.__module__ = module_name
        self.__file__ = os.path.join(sys.prefix, f'dummy_{module_name.replace(".", os.sep)}.py')

    def __call__(self, *args, **kwargs):
        return None

    def __getattr__(self, name: str) -> "DummyCallable":
        return DummyCallable(self.__module__)

    def __iter__(self) -> Iterator[None]:
        return iter([])

    def __len__(self) -> int:
        return 0

    def __getitem__(self, key):
        return None

    def __contains__(self, item) -> bool:
        return False


class DummyModule(ModuleType):
    """A dummy module that returns callable None objects for any attribute access."""

    def __init__(self, name: str):
        super().__init__(name)
        self._submodules: Dict[str, "DummyModule"] = {}

        # Create dummy paths
        dummy_dir = os.path.join(sys.prefix, f'dummy_{name.replace(".", os.sep)}')
        self.__file__ = os.path.join(dummy_dir, "__init__.py")
        self.__path__ = [dummy_dir]  # List of directories containing the package's submodules
        self.__package__ = name
        self.__all__ = []

    def __getattr__(self, name: str) -> Any:
        # Special case for __path__ to avoid recursion
        if name == "__path__":
            return [os.path.dirname(self.__file__)]

        if name in self._submodules:
            return self._submodules[name]

        if "." not in self.__name__:
            submodule_name = f"{self.__name__}.{name}"
        else:
            submodule_name = name

        if name[0].isupper() or "." in name:
            dummy_submodule = DummyModule(submodule_name)
            self._submodules[name] = dummy_submodule
            sys.modules[f"{self.__name__}.{name}"] = dummy_submodule
            return dummy_submodule

        dummy_callable = DummyCallable(self.__name__)
        self.__dict__[name] = dummy_callable
        return dummy_callable

    def __call__(self, *args, **kwargs):
        return None

    def __dir__(self) -> list:
        return list(self.__dict__.keys())

    def __repr__(self) -> str:
        return f"<dummy module '{self.__name__}'>"

    def __iter__(self) -> Iterator[None]:
        return iter([])


class ModuleImportFinder(MetaPathFinder):
    """Custom import finder to handle missing modules and their submodules."""

    @classmethod
    def _is_handled_module(cls, fullname: str) -> bool:
        """Check if this is a module or submodule we should handle."""
        parts = fullname.split(".")
        root_module = parts[0]

        # Check if the root module is a DummyModule
        if root_module in sys.modules and isinstance(sys.modules[root_module], DummyModule):
            return True

        return False

    @classmethod
    def find_spec(cls, fullname: str, path=None, target=None) -> Optional[ModuleSpec]:
        if not cls._is_handled_module(fullname):
            return None

        # Create parent modules if they don't exist
        parts = fullname.split(".")
        for i in range(1, len(parts)):
            parent_name = ".".join(parts[:i])
            if parent_name not in sys.modules:
                parent = DummyModule(parent_name)
                sys.modules[parent_name] = parent

        # Create a dummy spec that will trigger our loader
        spec = ModuleSpec(fullname, None)
        spec.loader = DummyModuleLoader(fullname)
        return spec


class DummyModuleLoader:
    """Loader for dummy modules that creates appropriate DummyModule instances."""

    def __init__(self, fullname: str):
        self.fullname = fullname

    def create_module(self, spec) -> DummyModule:
        """Create a new dummy module for this spec."""
        return DummyModule(self.fullname)

    def exec_module(self, module: DummyModule) -> None:
        """No execution needed for dummy modules."""
        pass


def safe_import(module_name: str, fallback_behavior: str = "dummy") -> Optional[ModuleType]:
    """
    Safely import a module, handling cases where the module is missing.

    Args:
        module_name: Name of the module to import
        fallback_behavior: How to handle missing modules:
            'dummy' - Return a dummy module that returns None for all attributes
            'warn' - Return dummy module and print warning
            'none' - Return None if module is missing

    Returns:
        The imported module or a dummy module based on fallback_behavior
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        if fallback_behavior == "none":
            return None

        # Create parent modules if they don't exist
        parts = module_name.split(".")
        for i in range(1, len(parts)):
            parent_name = ".".join(parts[:i])
            if parent_name not in sys.modules:
                parent = DummyModule(parent_name)
                sys.modules[parent_name] = parent

        dummy = DummyModule(module_name)

        log.debug("Warning: Module %s not found. Using dummy module.", module_name)

        sys.modules[module_name] = dummy
        return dummy


def setup_optional_module(module_name: str) -> None:
    """
    Set up an optional module in sys.modules before any imports happen.
    Should be called early in application startup.
    """
    if module_name not in sys.modules:
        # Register our custom finder if not already registered
        if not any(isinstance(finder, ModuleImportFinder) for finder in sys.meta_path):
            sys.meta_path.append(ModuleImportFinder)
        safe_import(module_name, fallback_behavior="warn")
