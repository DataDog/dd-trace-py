from types import ModuleType

import ddtrace.contrib.internal.subprocess.patch as subprocess_patch
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)


def _patch_subprocess(_module: ModuleType) -> None:
    # ensure that the subprocess patch is applied even after one click activation
    subprocess_patch.patch()
    log.debug("Patching common modules: subprocess_patch")


def patch() -> None:
    ModuleWatchdog.register_module_hook("subprocess", _patch_subprocess)


def unpatch() -> None:
    subprocess_patch.unpatch()
    ModuleWatchdog.unregister_module_hook("subprocess", _patch_subprocess)
