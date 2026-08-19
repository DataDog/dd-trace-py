"""Best-effort detection of code objects protected by a third-party bytecode
obfuscator (e.g. PyArmor).

Obfuscators such as PyArmor replace a function's real bytecode with an
encrypted blob that is only decrypted transiently, in place, for the duration
of a single call. Disassembling/rewriting such a code object with the
``bytecode`` package (or any tool that assumes ``co_code`` is genuine CPython
bytecode) can produce corrupt bytecode, and on some CPython versions merely
using the code object as a dict/cache key can crash if internal fields that
CPython expects to be consistent (e.g. PEP 669 monitoring data) are not, since
some obfuscation modes construct code objects outside of the normal
``PyCode_New*`` paths.

There is no version-proof structural signature for obfuscated code objects:
the more aggressive protection modes change the code object layout enough
that no marker survives. This module only recognizes the common case where
the PyArmor runtime has injected its ``__armor_enter__``/``__armor_exit__``
call wrapper around the function body. Callers that rewrite bytecode should
still guard the rewrite itself defensively, since this check can miss more
aggressive obfuscation modes.
"""

import sys
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.internal.threads import Lock


# Legacy PyArmor (<= 8.x, "RFT"/restrict mode) calls into module-level
# ``__armor_enter__``/``__armor_exit__`` functions that show up as regular
# globals, hence as names in ``co_names``.
_ARMOR_MARKERS = frozenset(("__armor_enter__", "__armor_exit__"))

# Modern PyArmor (9.x) instead embeds direct references to native runtime
# functions (e.g. ``C_ENTER_CO_OBJECT_INDEX``) in ``co_consts``. These are
# builtin functions whose ``__module__`` lives in the runtime package.
_RUNTIME_MODULE_PREFIXES = ("pytransform", "pyarmor_runtime")

# Once the obfuscation runtime is seen, it is never unloaded, so a positive
# result can be cached permanently. A negative result cannot: the runtime may
# be imported lazily, well after code that predates it has already been
# checked. Rather than re-scanning sys.modules on every call until it flips
# to True (expensive: O(loaded modules) per call, paid for the life of the
# process by the common, non-obfuscated case), a module watchdog observes
# future imports for us, so the fallback scan only has to run once.
_obfuscation_runtime_seen: bool = False
_obfuscation_watchdog_installed: bool = False
# Set to the watchdog class below once it has been installed, so that it can
# be uninstalled again as soon as it has served its purpose.
_obfuscation_runtime_watchdog_cls: t.Optional[type[BaseModuleWatchdog]] = None
# Guards the one-time sys.modules scan/watchdog install below, so a concurrent
# caller cannot observe the in-progress state as a confirmed negative (i.e.
# get a stale ``False`` before the scan/watchdog install has completed).
_init_lock = Lock()


def _mark_obfuscation_runtime_seen() -> None:
    global _obfuscation_runtime_seen
    _obfuscation_runtime_seen = True
    if _obfuscation_runtime_watchdog_cls is not None:
        _obfuscation_runtime_watchdog_cls.uninstall()


def _runtime_seen() -> bool:
    # Routing the read through a function call (rather than inlining the
    # global lookup) stops type checkers from narrowing it to a fixed literal
    # across the ``with _init_lock`` block below: another thread can flip it
    # while we wait for the lock, so the two checks in
    # ``_obfuscation_runtime_loaded`` are not guaranteed to agree.
    return _obfuscation_runtime_seen


def _obfuscation_runtime_loaded() -> bool:
    global _obfuscation_watchdog_installed
    global _obfuscation_runtime_watchdog_cls

    if _runtime_seen():
        return True

    with _init_lock:
        # Re-check: another thread may have finished initialization (or found
        # the runtime) while we were waiting for the lock.
        if _runtime_seen():
            return True

        if _obfuscation_watchdog_installed:
            return False

        _obfuscation_watchdog_installed = True

        # Snapshot the keys: sys.modules can mutate (e.g. a concurrent
        # import) as we iterate it. This is the one, unavoidable full scan:
        # from here on, the watchdog below observes new imports instead of us
        # re-scanning.
        if any(name.startswith(_RUNTIME_MODULE_PREFIXES) for name in tuple(sys.modules)):
            _mark_obfuscation_runtime_seen()
            return True

        class _ObfuscationRuntimeWatchdog(BaseModuleWatchdog):
            def after_import(self, module: ModuleType) -> None:
                name = getattr(module, "__name__", None)
                if isinstance(name, str) and name.startswith(_RUNTIME_MODULE_PREFIXES):
                    _mark_obfuscation_runtime_seen()

        _obfuscation_runtime_watchdog_cls = _ObfuscationRuntimeWatchdog
        _obfuscation_runtime_watchdog_cls.install()

        return False


def _is_runtime_object(obj: object) -> bool:
    module = getattr(obj, "__module__", None)
    if isinstance(module, ModuleType):
        module = module.__name__
    return isinstance(module, str) and module.startswith(_RUNTIME_MODULE_PREFIXES)


class ObfuscatedCodeError(Exception):
    """Raised when a code object cannot be safely rewritten because it appears
    to be obfuscated (e.g. by PyArmor).
    """


def is_obfuscated_code(code: CodeType) -> bool:
    """Check whether a code object looks like it was wrapped by PyArmor.

    This is a coarse, best-effort heuristic, not a guarantee: it can produce
    false negatives (some obfuscation modes leave no detectable marker), but
    should not produce false positives.
    """
    # Cheap gate: only bother inspecting the code object if an obfuscation
    # runtime is actually loaded in the process.
    if not _obfuscation_runtime_loaded():
        return False

    return not _ARMOR_MARKERS.isdisjoint(code.co_names) or any(_is_runtime_object(c) for c in code.co_consts)
