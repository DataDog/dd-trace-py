import sys
from types import ModuleType
from typing import Any
from typing import Set
from typing import Tuple

from ..compat import PYTHON_VERSION_INFO
from ..module import BaseModuleWatchdog


NEW_MODULES: Set[str] = set()  # New modules that have been imported since the last check
ALL_MODULES: Set[str] = set()  # All modules that have been imported
MODULE_HOOK_INSTALLED = False

# For Python >= 3.8 we can use the sys.audit event import(module, filename, sys.path, sys.meta_path, sys.path_hooks)
if PYTHON_VERSION_INFO >= (3, 8):

    def audit_hook(event: str, args: Tuple[Any, ...]):
        if event != "import":
            return

        global NEW_MODULES, ALL_MODULES
        NEW_MODULES.add(args[0])
        ALL_MODULES.add(args[0])

    def get_newly_imported_modules() -> Set[str]:
        global MODULE_HOOK_INSTALLED, NEW_MODULES, ALL_MODULES

        # Our hook is not installed, so we are not getting notified of new imports,
        # we need to track the changes manually
        if not NEW_MODULES and not MODULE_HOOK_INSTALLED:
            latest_modules = set(sys.modules.keys())
            NEW_MODULES = latest_modules - ALL_MODULES
            ALL_MODULES = latest_modules

        new_modules = NEW_MODULES
        NEW_MODULES = set()
        return new_modules

    def install_import_hook():
        global MODULE_HOOK_INSTALLED, NEW_MODULES, ALL_MODULES

        # If we have not called get_newly_imported_modules yet, we can initialize to all imported modules
        if not NEW_MODULES:
            NEW_MODULES = set(sys.modules.keys())
            ALL_MODULES = NEW_MODULES.copy()
        sys.addaudithook(audit_hook)
        MODULE_HOOK_INSTALLED = True

    def uninstall_import_hook():
        # We cannot uninstall a sys audit hook
        pass

else:

    class TelemetryWriterModuleWatchdog(BaseModuleWatchdog):
        _initial = True
        _new_imported: Set[str] = set()

        def after_import(self, module: ModuleType) -> None:
            self._new_imported.add(module.__name__)

        @classmethod
        def get_new_imports(cls):
            if cls._initial:
                try:
                    # On the first call, use sys.modules to cover all imports before we started. This is not
                    # done on __init__ because we want to do this slow operation on the writer's periodic call
                    # and not on instantiation.
                    new_imports = list(sys.modules.keys())
                except RuntimeError:
                    new_imports = []
                finally:
                    # If there is any problem with the above we don't want to repeat this slow process, instead we just
                    # switch to report new dependencies on further calls
                    cls._initial = False
            else:
                new_imports = list(cls._new_imported)

            cls._new_imported.clear()
            return new_imports

    def get_newly_imported_modules() -> Set[str]:
        return set(TelemetryWriterModuleWatchdog.get_new_imports())

    def install_import_hook():
        if not TelemetryWriterModuleWatchdog.is_installed():
            TelemetryWriterModuleWatchdog.install()

    def uninstall_import_hook():
        if TelemetryWriterModuleWatchdog.is_installed():
            TelemetryWriterModuleWatchdog.uninstall()
