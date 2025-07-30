"""
IAST (Interactive Application Security Testing) Product Entry Point

This module serves as the main entry point for IAST instrumentation and addresses critical
compatibility issues with Gevent-based applications.

=== GEVENT COMPATIBILITY ===

Applications using Gunicorn with the Gevent worker class may experience random worker timeouts
during shutdown sequences when IAST is enabled. This occurs because IAST's dynamic code
instrumentation interferes with Gevent's monkey patching mechanism.

Root Cause:
-----------
IAST relies on modules like `importlib.metadata`, `importlib`, `subprocess`, and `inspect`
which, when loaded at module level, cannot be properly released from memory. This creates
conflicts between the in-memory versions of these modules and Gevent's monkey patching,
leading to sporadic blocking operations that can cause worker timeouts.


Caveat:
Adding incorrect top-level imports (especially `importlib.metadata`, `inspect`, or
`subprocess`) could reintroduce the flaky gevent timeout errors. Always import these
modules locally within functions when needed.
"""

import sys

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def _drop_safe(module):
    """
    Safely remove a module from sys.modules to prevent gevent conflicts.

    Modules like `importlib.metadata` and `inspect` must be removed from memory
    after IAST initialization to avoid conflicts with Gevent's monkey patching.
    If these modules remain loaded, they can interfere with Gevent's concurrency
    model and cause sporadic worker timeouts in Gunicorn applications.

    Args:
        module: Name of the module to remove from sys.modules
    """
    try:
        del sys.modules[module]
    except KeyError:
        log.debug("IAST: %s module wasn't loaded, drop from sys.modules not needed", module)


def post_preload():
    """
    Initialize IAST instrumentation during the preload phase.

    This function runs early in the application lifecycle (before Gevent's
    cleanup_loaded_modules if present) to ensure IAST instrumentation is
    properly established without interfering with Gevent's monkey patching.

    The initialization includes:
    1. Enabling IAST propagation (AST-based taint tracking)
    2. Patching taint sink points for vulnerability detection
    3. Cleaning up problematic modules from memory

    This early initialization is critical for Gevent compatibility and prevents
    random worker timeouts that can occur when IAST modules conflict with
    Gevent's concurrency mechanisms.
    """
    if asm_config._iast_enabled:
        # Import locally to avoid early module loading conflicts
        from ddtrace.appsec._iast import enable_iast_propagation
        from ddtrace.appsec._iast.main import patch_iast

        log.debug("Enabling IAST by auto import")
        enable_iast_propagation()
        patch_iast()

        # Remove modules that can conflict with Gevent's monkey patching
        # These modules must be cleaned up to prevent memory conflicts that
        # lead to sporadic worker timeouts in Gevent-based applications
        _drop_safe("importlib.metadata")  # Used by native C extensions, conflicts with gevent
        _drop_safe("inspect")  # Used by taint sinks, must be imported locally


def start():
    """
    Start the IAST product.

    Currently a no-op as all initialization happens in post_preload().
    """
    pass


def restart(join=False):
    """
    Restart the IAST product.
    """
    pass


def stop(join=False):
    """
    Stop the IAST product.
    """
    pass


def at_exit(join=False):
    """
    Clean up IAST product at application exit.
    """
    pass
