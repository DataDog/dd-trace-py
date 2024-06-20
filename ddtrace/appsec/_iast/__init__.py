"""IAST (interactive application security testing) analyzes code for security vulnerabilities.

To add new vulnerabilities analyzers (Taint sink) we should update `IAST_PATCH` in
`ddtrace/appsec/iast/_patch_modules.py`

Create new file with the same name: `ddtrace/appsec/iast/taint_sinks/[my_new_vulnerability].py`

Then, implement the `patch()` function and its wrappers.

In order to have the better performance, the Overhead control engine (OCE) helps us to control the overhead of our
wrapped functions. We should create a class that inherit from `ddtrace.appsec._iast.taint_sinks._base.VulnerabilityBase`
and register with `ddtrace.appsec._iast.oce`.

@oce.register
class MyVulnerability(VulnerabilityBase):
    vulnerability_type = "MyVulnerability"
    evidence_type = "kind_of_Vulnerability"

Before that, we should decorate our wrappers with `wrap` method and
report the vulnerabilities with `report` method. OCE will manage the number of requests, number of vulnerabilities
to reduce the overhead.

@WeakHash.wrap
def wrapped_function(wrapped, instance, args, kwargs):
    WeakHash.report(
        evidence_value=evidence,
    )
    return wrapped(*args, **kwargs)
"""  # noqa: RST201, RST213, RST210
import inspect
import os
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils.formats import asbool

from ._overhead_control_engine import OverheadControl
from ._utils import _is_iast_enabled


log = get_logger(__name__)

oce = OverheadControl()


def ddtrace_iast_flask_patch():
    """
    Patch the code inside the Flask main app source code file (typically "app.py") so
    IAST/Custom Code propagation works also for the functions and methods defined inside it.
    This must be called on the top level or inside the `if __name__ == "__main__"`
    and must be before the `app.run()` call. It also requires `DD_IAST_ENABLED` to be
    activated.
    """
    if not _is_iast_enabled():
        return

    from ._ast.ast_patching import astpatch_module

    module_name = inspect.currentframe().f_back.f_globals["__name__"]
    module = sys.modules[module_name]
    try:
        module_path, patched_ast = astpatch_module(module, remove_flask_run=True)
    except Exception:
        log.debug("Unexpected exception while AST patching", exc_info=True)
        return

    compiled_code = compile(patched_ast, module_path, "exec")
    exec(compiled_code, module.__dict__)  # nosec B102
    sys.modules[module_name] = compiled_code


def enable_iast_propagation():
    if asbool(os.getenv("DD_IAST_ENABLED", False)):
        from ddtrace.appsec._iast._utils import _is_python_version_supported

        if _is_python_version_supported():
            from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
            from ddtrace.appsec._iast._loader import _exec_iast_patched_module

            log.debug("IAST enabled")
            ModuleWatchdog.register_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)


__all__ = [
    "oce",
    "ddtrace_iast_flask_patch",
    "enable_iast_propagation",
]
