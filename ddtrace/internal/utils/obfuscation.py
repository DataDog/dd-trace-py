"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.utils.obfuscation``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. (``import_module``, not ``import x.y as z``: a package attribute can shadow
a submodule, as ``settings.env`` does.) The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

from importlib import import_module
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.utils.obfuscation import _ARMOR_MARKERS as _ARMOR_MARKERS  # noqa: F401
    from _ddtrace_internal.utils.obfuscation import _RUNTIME_MODULE_PREFIXES as _RUNTIME_MODULE_PREFIXES  # noqa: F401
    from _ddtrace_internal.utils.obfuscation import ObfuscatedCodeError as ObfuscatedCodeError  # noqa: F401
    from _ddtrace_internal.utils.obfuscation import _is_runtime_object as _is_runtime_object  # noqa: F401
    from _ddtrace_internal.utils.obfuscation import is_obfuscated_code as is_obfuscated_code  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = import_module("_ddtrace_internal.utils.obfuscation")
