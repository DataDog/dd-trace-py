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

from ddtrace.internal.utils.cache import callonce


# Legacy PyArmor (<= 8.x, "RFT"/restrict mode) calls into module-level
# ``__armor_enter__``/``__armor_exit__`` functions that show up as regular
# globals, hence as names in ``co_names``.
_ARMOR_MARKERS = frozenset(("__armor_enter__", "__armor_exit__"))

# Modern PyArmor (9.x) instead embeds direct references to native runtime
# functions (e.g. ``C_ENTER_CO_OBJECT_INDEX``) in ``co_consts``. These are
# builtin functions whose ``__module__`` lives in the runtime package.
_RUNTIME_MODULE_PREFIXES = ("pytransform", "pyarmor_runtime")


@callonce
def _obfuscation_runtime_loaded() -> bool:
    return any(name.startswith(_RUNTIME_MODULE_PREFIXES) for name in sys.modules)


def _is_runtime_object(obj: object) -> bool:
    module = getattr(obj, "__module__", None)
    if isinstance(module, ModuleType):
        module = module.__name__
    return isinstance(module, str) and module.startswith(_RUNTIME_MODULE_PREFIXES)


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
