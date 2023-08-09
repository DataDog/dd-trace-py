"""IAST (interactive application security testing) analyzes code for security vulnerabilities.

To add new vulnerabilities analyzers (Taint sink) we should update `IAST_PATCH` in
`ddtrace/appsec/iast/_patch_modules.py`

Create new file with the same name: `ddtrace/appsec/iast/taint_sinks/[my_new_vulnerability].py`

Then, implement the `patch()` function and its wrappers.

In order to have the better performance, the Overhead control engine (OCE) helps us to control the overhead of our
wrapped functions. We should create a class that inherit from `ddtrace.appsec.iast.taint_sinks._base.VulnerabilityBase`
and register with `ddtrace.appsec.iast.oce`.

@oce.register
class MyVulnerability(VulnerabilityBase):
    vulnerability_type = "MyVulnerability"
    evidence_type = "kind_of_Vulnerability"

Before that, we should decorate our wrappers with `wrap` method and
report the vulnerabilities with `report` method. OCE will manage the number of requests, number of vulnerabilities
to reduce the overhead.

@WeakHash.wrap
def wrapped_function(wrapped, instance, args, kwargs):
    # type: (Callable, str, Any, Any, Any) -> Any
    WeakHash.report(
        evidence_value=evidence,
    )
    return wrapped(*args, **kwargs)
"""  # noqa: RST201, RST213, RST210
import inspect
import sys

from ddtrace.appsec.iast._ast.ast_patching import astpatch_module
from ddtrace.appsec.iast._loader import IS_IAST_ENABLED
from ddtrace.appsec.iast._overhead_control_engine import OverheadControl
from ddtrace.internal.logger import get_logger

log = get_logger(__name__)

oce = OverheadControl()


def ddtrace_iast_flask_patch():
    log.warning("JJJ patched start")
    if not IS_IAST_ENABLED:
        log.warning("JJJ patched 1")
        return

    log.warning("JJJ patched 2")
    module_name = inspect.currentframe().f_back.f_globals["__name__"]
    log.warning("JJJ patched 3")
    module = sys.modules[module_name]
    log.warning("JJJ patched 4")
    try:
        log.warning("JJJ patched 5")
        # JJJ: remove app.run() remove under if __main__?
        module_path, patched_ast = astpatch_module(module)
        # log.warning("JJJ patched 6, patched_ast: \n%s" % patched_ast)
    except Exception:
        log.warning("JJJ patched 7")
        log.debug("Unexpected exception while AST patching", exc_info=True)
        return

    log.warning("JJJ patched 8")
    compiled_code = compile(patched_ast, module_path, "exec")
    log.warning("JJJJ type compiled_code: %s" % type(compiled_code))
    log.warning("JJJ patched 9, module dict: %s" % module.__dict__)
    exec(compiled_code, module.__dict__)  # nosec B102
    # sys.modules[module_name] = compiled_code
    log.warning("JJJ patched end")


__all__ = [
    "oce",
    "ddtrace_iast_flask_patch",
]
