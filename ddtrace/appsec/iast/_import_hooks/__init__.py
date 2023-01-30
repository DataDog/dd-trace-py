#!/usr/bin/env python3
from importlib.abc import Loader
from importlib.abc import MetaPathFinder
import importlib.util
import os
import sys
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Union

from ddtrace.appsec.iast._ast import ast_patching


_PY37 = sys.version_info >= (3, 7, 0)
if _PY37:
    from importlib.abc import ResourceReader
else:
    ResourceReader = Any  # type: ignore

ModuleCallback = Callable[[ModuleType], None]

# noinspection PyDeprecation
find_spec = importlib.util.find_spec

_import_hooks_initialized = False

# A global dictionary of import hooks.
_import_hooks = {}  # type: Dict[str, Optional[List[ModuleCallback]]]


def _apply_hooks(module, module_name):  # type: (Any, str) -> None
    # Note: this re-reads the source so any runtime changes (like module.foo=1)
    # will be lost so this must be run first before applying the import hooks
    hooks = _import_hooks.get(module_name, None)
    if hooks is not None:
        _import_hooks[module_name] = None
        for callable_ in hooks:
            callable_(module)

    return module


def _get_old_path(module_file):  # type: (str) -> List[str]
    prev_part, last_part = os.path.split(module_file)
    if not last_part.startswith("__init__.py"):
        return []

    last_len = len(last_part)

    if prev_part:
        # Add 1 to account for the slash before the /__init__.py
        last_len += 1

    return [module_file[:-last_len]]


def _create_and_load_module_from_source(
    loader,  # type: Loader
    module_name,  # type: str
    module_path,  # type: str
    new_source,  # type: str
):  # type: (...) -> ModuleType
    module = ModuleType(module_name)

    module.__file__ = module_path
    is_package = False

    if module_path.endswith("__init__.py"):
        is_package = True
        module.__package__ = module_name
        module.__path__ = _get_old_path(module_path)

    # Python 3 only
    # if sys.version_info[0] >= 3:
    module.__loader__ = loader
    module.__spec__ = importlib.util.spec_from_loader(module_name, loader, is_package=is_package)

    # This must be before the exec because some modules check for their own
    # existence in sys.modules (like django.apps.registry)
    sys.modules[module_name] = module

    module_globals = module.__dict__

    compiled_code = compile(new_source, module_path, "exec")
    exec(compiled_code, module_globals)
    return module


class _ImportHookPy3Loader(Loader):
    __slots__ = ["_loader"]

    def __init__(self, loader):  # type: (Any) -> None
        self._loader = loader

    def __get_module(self, fullname):  # type: (str) -> Optional[ModuleType]
        try:
            return sys.modules[fullname]
        except KeyError:
            return None

    def is_package(self, fullname):  # type: (str) -> bool
        """
        FIX error with Flask:
        AttributeError: _ImportHookPy2Loader.is_package() method is missing but is required by
        Flask of PEP 302 import hooks.  If you do not use import hooks and you encounter this
        error please file a bug against Flask.

        Return true, if the named module is a package.

        We need this method to get correct spec objects with
        Python 3.4 (see PEP451)
        """
        return hasattr(self.__get_module(fullname), "__path__")

    def get_resource_reader(self, fullname):  # type: (Any) -> Optional[ResourceReader]
        """
        Return the package's loader if it's a ResourceReader.
        """
        reader = getattr(self._loader, "get_resource_reader", None)
        if reader is None:
            return None
        return reader(fullname)

    def get_filename(self, fullname=""):  # type: (str) -> str
        """Soooo weird, isn't it? this crazy function follow the behavior of :
        - importlib._bootstrap_external:spec_from_file_location
        - importlib.abc:Loader
        """
        del fullname  # Get rid of unused variable
        try:
            return self._loader.path
        except AttributeError:
            raise ImportError

    def get_data(self, pathname):  # type: (str) -> bytes
        with open(pathname, "rb") as file:
            return file.read()

    def normal_load_module(self, module_name):  # type: (str) -> ModuleType
        return self._loader.load_module(module_name)

    def load_module(self, module_name):  # type: (str) -> Any
        """Each time you write a "from xyz import" or "import yyy", python raise the method
        "find_module" for each sys.meta_path element.

        The methods find_module call load_module if the module exists without raise an exception

        we use _loader (SourceFileLoader) instead of imp for this reasons:
        - imp is deprecated in Python 3
        - _loader contains the "parent" of the module, if we import `from .base_events import *`
          importlib musts know the parent to resolve the path

        """
        module_updated = False
        module = None
        if ("ddtrace" not in module_name) or ("tests.fixtures" in module_name) and module_name not in sys.modules:

            module_path, new_source = ast_patching.astpatch_source(
                module_name=module_name,
            )

            if new_source:
                module = _create_and_load_module_from_source(self, module_name, module_path, new_source)
                module_updated = True

        if not module_updated:
            # Binary, filtered or built-in
            module = self.normal_load_module(module_name)

        return _apply_hooks(module, module_name)


class ImportHookFinder(MetaPathFinder):
    """
    A sys.meta_path finder.

    See PEP 302 (Python 2.3+).
    """

    __slots__ = ["_skip"]

    def __init__(self):  # type: () -> None
        self._skip = set()  # type: Set[str]

    def find_module(
        self,
        fullname,  # type: str
        path=None,  # type: Optional[Sequence[Union[bytes, str]]]
    ):  # type: (...) -> Optional[Loader]
        """
        The behavior of this method is described in Python PEP-302.

        We need to call import again to get the original module.
        So we'll temporarily skip the module to avoid an infinite loop.
        """
        del path  # Get rid of unused variable

        if fullname in self._skip or (fullname.startswith("ddtrace.") and ".tests.fixtures" not in fullname):
            return None

        self._skip.add(fullname)
        result_loader = None  # type: Optional[Loader]

        assert callable(find_spec)
        spec = find_spec(fullname)
        if spec and spec.loader:
            result_loader = _ImportHookPy3Loader(spec.loader)
        return result_loader


def initialize_iast_import_hooks():  # type: () -> None
    """Add ImportHookFinder to sys.meta_path.

    It means that each time you write a "from xyz import" or "import yyy", python raise the method
    "find_module" for each sys.meta_path element.

    is ImportHookFinder inserted in meta_path pos 1? Why not `append` or insert(0)?
    If we insert the ImportHookFinder as append the hooks aren't triggered and if
    ImportHookFinder is in pos 0, could raise an AssertionError with Python 3, is better if
    the first importer is importlib, make some checks and validations
     and then our ImportHookFinder

    we removed imp.acquire_lock (https://docs.python.org/3/library/imp.html#imp.acquire_lock) in
    this function because is deprecated in Python 3 and has worst performance (Unit test
    2min 25s -> 53s). Python 3 doesn't need it and Python 2 doesn't changes the behavior without
    it.
    """
    global _import_hooks_initialized
    if _import_hooks_initialized:
        return

    sys.meta_path.insert(1, ImportHookFinder())
    _import_hooks_initialized = True
