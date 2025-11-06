"""IAST (Interactive Application Security Testing) analyzes code for security vulnerabilities.

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
"""

import os
import sys
import types

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.settings.asm import config as asm_config

from ._listener import iast_listen
from ._overhead_control_engine import oce


log = get_logger(__name__)

_IAST_TO_BE_LOADED = True
_iast_propagation_enabled = False
_fork_handler_registered = False


def _disable_iast_after_fork():
    """
    Disable IAST in forked child processes to prevent segmentation faults.

    When a process forks, the native extension's internal state (including memory
    mappings, taint maps, context slots, and object pools) cannot be safely verified
    or reconstructed in the child process. Attempting to use IAST in a forked child
    can lead to segmentation faults due to corrupted shared_ptr references, invalid
    mutex states, and cross-process memory corruption.

    This is critical for multiprocessing compatibility. Rather than
    attempting to reset and reuse the native extension state (which is complex and
    error-prone), we simply disable IAST in the child process. This ensures safety
    at the cost of not having IAST coverage in forked workers.

    The child process:
    - Clears all C++ taint maps and context slots
    - Resets the Python-level IAST_CONTEXT
    - Disables IAST by setting asm_config._iast_enabled = False

    This prevents any IAST operations from running in the child, ensuring no
    segmentation faults occur from accessing corrupted native state.
    """
    if not asm_config._iast_enabled:
        return

    try:
        # Import locally to avoid issues if the module hasn't been loaded yet
        from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
        from ddtrace.appsec._iast._taint_tracking._context import clear_all_request_context_slots

        log.debug("Resetting IAST taint tracking state after fork")

        # Clear C++ side: all taint maps and context slots
        clear_all_request_context_slots()

        # Clear Python side: reset the context ID so child creates a new one
        IAST_CONTEXT.set(None)
        asm_config._iast_enabled = False

    except Exception as e:
        log.debug("Error resetting IAST state after fork: %s", e, exc_info=True)


def _register_fork_handler():
    """
    Register the fork handler if IAST is enabled and it hasn't been registered yet.

    This is called during IAST initialization to ensure the fork handler is only
    registered once and only when IAST is actually being used.
    """
    global _fork_handler_registered

    if not _fork_handler_registered and asm_config._iast_enabled:
        forksafe.register(_disable_iast_after_fork)
        _fork_handler_registered = True
        log.debug("IAST fork safety handler registered")


def ddtrace_iast_flask_patch():
    """
    Patch the code inside the Flask main app source code file (typically "app.py") so
    Runtime Code Analysis (IAST) works also for the functions and methods defined inside it.
    This must be called on the top level or inside the `if __name__ == "__main__"`
    and must be before the `app.run()` call. It also requires `DD_IAST_ENABLED` to be
    activated.
    """
    if not asm_config._iast_enabled:
        return

    # Import inspect locally to avoid gevent compatibility issues.
    # Top-level imports of inspect can interfere with gevent's monkey patching
    # and cause sporadic worker timeouts in Gunicorn applications.
    # See ddtrace/internal/iast/product.py for detailed explanation.
    import inspect

    from ._ast.ast_patching import astpatch_module

    module_name = inspect.currentframe().f_back.f_globals["__name__"]
    module = sys.modules[module_name]
    try:
        module_path, patched_ast = astpatch_module(module)
    except Exception:
        log.debug("Unexpected exception while AST patching", exc_info=True)
        return

    if not patched_ast:
        log.debug("Main flask module not patched, probably it was not needed")
        return

    compiled_code = compile(patched_ast, module_path, "exec")
    # creating a new module environment to execute the patched code from scratch
    new_module = types.ModuleType(module_name)
    module.__dict__.clear()
    module.__dict__.update(new_module.__dict__)
    # executing the compiled code in the new module environment
    exec(compiled_code, module.__dict__)  # nosec B102


def enable_iast_propagation():
    """Add IAST AST patching in the ModuleWatchdog"""
    # DEV: These imports are here to avoid _ast.ast_patching import in the top level
    # because they are slow and affect serverless startup time
    if asm_config._iast_propagation_enabled:
        from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
        from ddtrace.appsec._iast._loader import _exec_iast_patched_module

        global _iast_propagation_enabled
        if _iast_propagation_enabled:
            return

        log.debug("iast::instrumentation::starting IAST")
        ModuleWatchdog.register_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)
        _iast_propagation_enabled = True
        _register_fork_handler()


def _iast_pytest_activation():
    global _iast_propagation_enabled
    if _iast_propagation_enabled:
        return
    os.environ["DD_IAST_ENABLED"] = os.environ.get("DD_IAST_ENABLED") or "1"
    os.environ["DD_IAST_REQUEST_SAMPLING"] = os.environ.get("DD_IAST_REQUEST_SAMPLING") or "100.0"
    os.environ["_DD_APPSEC_DEDUPLICATION_ENABLED"] = os.environ.get("_DD_APPSEC_DEDUPLICATION_ENABLED") or "false"
    os.environ["DD_IAST_VULNERABILITIES_PER_REQUEST"] = os.environ.get("DD_IAST_VULNERABILITIES_PER_REQUEST") or "1000"
    os.environ["DD_IAST_MAX_CONCURRENT_REQUESTS"] = os.environ.get("DD_IAST_MAX_CONCURRENT_REQUESTS") or "1000"

    asm_config._iast_request_sampling = 100.0
    asm_config._deduplication_enabled = False
    asm_config._iast_max_vulnerabilities_per_requests = 1000
    asm_config._iast_max_concurrent_requests = 1000
    oce.reconfigure()


def disable_iast_propagation():
    """Remove IAST AST patching from the ModuleWatchdog. Only for testing proposes"""
    # DEV: These imports are here to avoid _ast.ast_patching import in the top level
    # because they are slow and affect serverless startup time
    from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
    from ddtrace.appsec._iast._loader import _exec_iast_patched_module

    global _iast_propagation_enabled
    if not _iast_propagation_enabled:
        return
    try:
        ModuleWatchdog.remove_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)
    except KeyError:
        log.warning("IAST is already disabled and it's not in the ModuleWatchdog")
    _iast_propagation_enabled = False


__all__ = [
    "ddtrace_iast_flask_patch",
    "enable_iast_propagation",
    "disable_iast_propagation",
]


def load_iast():
    """Lazily load the iast module listeners."""
    global _IAST_TO_BE_LOADED
    if _IAST_TO_BE_LOADED:
        _register_fork_handler()
        iast_listen()
        _IAST_TO_BE_LOADED = False
