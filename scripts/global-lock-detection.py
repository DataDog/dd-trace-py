import ddtrace.auto  # noqa

from time import sleep
import gc
from types import ModuleType
from threading import Lock
import sys

# These modules are known to have global locks. They are either safe to ignore
# or we currently have no way to avoid them.
SAFE_MODULES = {
    "__main__",
    "asyncio.events",
    "asyncio.mixins",
    "tempfile",
    "threading",
}


# Sleep long enough to allow background threads to complete imports.
sleep(60)

LockClass = type(Lock())


def modules_for_lock(lock):
    return {
        _
        for d in (_ for _ in gc.get_referrers(lock) if isinstance(_, dict))
        for _ in gc.get_referrers(d)
        if isinstance(_, ModuleType)
    }


# Collect all objects before querying the garbage collector for locks.
gc.collect()

modules_with_global_lock = set()
for lock in (_ for _ in gc.get_objects() if isinstance(_, LockClass)):
    modules = modules_for_lock(lock)
    if not modules:
        continue

    modules_with_global_lock.update(modules)


modules_with_global_lock = {_ for _ in modules_with_global_lock if not _.__name__.startswith("ddtrace")} - {
    sys.modules.get(_) for _ in SAFE_MODULES
}
if modules_with_global_lock:
    raise RuntimeError(
        "Detected a global lock in the following modules:\n"
        + "\n".join(sorted(m.__name__ for m in modules_with_global_lock))
    )
