"""
This is the entry point for the IAST instrumentation. `enable_iast_propagation` is called on patch_all function
too but patch_all depends of DD_TRACE_ENABLED environment variable. This is the reason why we need to call it
here and it's not a duplicate call due to `enable_iast_propagation` has a global variable to avoid multiple calls.
"""
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def post_preload():
    if asm_config._iast_enabled:
        from ddtrace.appsec._iast import enable_iast_propagation

        log.debug("Enabling the IAST by auto import")
        enable_iast_propagation()

        del sys.modules["importlib.metadata"]


def start():
    pass


def restart(join=False):
    pass


def stop(join=False):
    pass


def at_exit(join=False):
    pass
