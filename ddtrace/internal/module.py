"""Legacy re-export shim for a moved module.

``ddtrace.internal.module`` moved to:

``ddtrace_base.module``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.module import BaseModuleWatchdog as BaseModuleWatchdog  # noqa: F401
from ddtrace_base.module import ImportExceptionHookCond as ImportExceptionHookCond  # noqa: F401
from ddtrace_base.module import ImportExceptionHookType as ImportExceptionHookType  # noqa: F401
from ddtrace_base.module import ModuleHookType as ModuleHookType  # noqa: F401
from ddtrace_base.module import ModuleWatchdog as ModuleWatchdog  # noqa: F401
from ddtrace_base.module import PreExecHookCond as PreExecHookCond  # noqa: F401
from ddtrace_base.module import PreExecHookType as PreExecHookType  # noqa: F401
from ddtrace_base.module import TransformerType as TransformerType  # noqa: F401
from ddtrace_base.module import _resolve as _resolve  # noqa: F401
from ddtrace_base.module import _UniversalModuleWatchdog as _UniversalModuleWatchdog  # noqa: F401
from ddtrace_base.module import find_loader as find_loader  # noqa: F401
from ddtrace_base.module import is_module_installed as is_module_installed  # noqa: F401
from ddtrace_base.module import is_namespace_spec as is_namespace_spec  # noqa: F401
from ddtrace_base.module import log as log  # noqa: F401
from ddtrace_base.module import origin as origin  # noqa: F401
from ddtrace_base.module import register_post_run_module_hook as register_post_run_module_hook  # noqa: F401
from ddtrace_base.module import register_run_module_transformer as register_run_module_transformer  # noqa: F401
from ddtrace_base.module import unregister_post_run_module_hook as unregister_post_run_module_hook  # noqa: F401
from ddtrace_base.module import unregister_run_module_transformer as unregister_run_module_transformer  # noqa: F401
