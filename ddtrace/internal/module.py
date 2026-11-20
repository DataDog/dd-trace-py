"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.module``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.module as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.module import BaseModuleWatchdog as BaseModuleWatchdog  # noqa: F401
    from _ddtrace_internal.module import ImportExceptionHookCond as ImportExceptionHookCond  # noqa: F401
    from _ddtrace_internal.module import ImportExceptionHookType as ImportExceptionHookType  # noqa: F401
    from _ddtrace_internal.module import ModuleHookType as ModuleHookType  # noqa: F401
    from _ddtrace_internal.module import ModuleWatchdog as ModuleWatchdog  # noqa: F401
    from _ddtrace_internal.module import PreExecHookCond as PreExecHookCond  # noqa: F401
    from _ddtrace_internal.module import PreExecHookType as PreExecHookType  # noqa: F401
    from _ddtrace_internal.module import TransformerType as TransformerType  # noqa: F401
    from _ddtrace_internal.module import _ImportHookChainedLoader as _ImportHookChainedLoader  # noqa: F401
    from _ddtrace_internal.module import _patch_run_code as _patch_run_code  # noqa: F401
    from _ddtrace_internal.module import _post_run_module_hooks as _post_run_module_hooks  # noqa: F401
    from _ddtrace_internal.module import _resolve as _resolve  # noqa: F401
    from _ddtrace_internal.module import _run_code as _run_code  # noqa: F401
    from _ddtrace_internal.module import _UniversalModuleWatchdog as _UniversalModuleWatchdog  # noqa: F401
    from _ddtrace_internal.module import _wrapped_run_code as _wrapped_run_code  # noqa: F401
    from _ddtrace_internal.module import find_loader as find_loader  # noqa: F401
    from _ddtrace_internal.module import is_module_installed as is_module_installed  # noqa: F401
    from _ddtrace_internal.module import is_namespace_spec as is_namespace_spec  # noqa: F401
    from _ddtrace_internal.module import log as log  # noqa: F401
    from _ddtrace_internal.module import origin as origin  # noqa: F401
    from _ddtrace_internal.module import register_post_run_module_hook as register_post_run_module_hook  # noqa: F401
    from _ddtrace_internal.module import (
        unregister_post_run_module_hook as unregister_post_run_module_hook,  # noqa: F401
    )

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
