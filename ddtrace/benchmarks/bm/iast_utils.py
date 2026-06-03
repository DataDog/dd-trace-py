import contextlib

from bm.utils import override_env


try:
    from ddtrace.internal.settings.asm import config as asm_config
except ImportError:
    # legacy import
    from ddtrace.settings.asm import config as asm_config

try:
    # >= 3.15
    from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request as end_iast_context
    from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request as iast_start_request

except ImportError:
    try:
        # >= 3.6; < 3.15
        from ddtrace.appsec._iast._iast_request_context_base import end_iast_context
        from ddtrace.appsec._iast._iast_request_context_base import start_iast_context as iast_start_request
    except ImportError:
        # < 3.6
        from ddtrace.appsec._iast._iast_request_context import end_iast_context
        from ddtrace.appsec._iast._iast_request_context import iast_start_request

try:
    # >= 3.6; < 3.15
    from ddtrace.appsec._iast._iast_request_context_base import set_iast_request_enabled
except ImportError:
    try:
        # < 3.6
        from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
    except ImportError:
        # >= 3.15
        set_iast_request_enabled = lambda x: None  # noqa: E731

try:
    from ddtrace.appsec._iast._overhead_control_engine import oce
except ImportError:
    # legacy import
    from ddtrace.appsec._iast import oce


def _start_iast_context_and_oce():
    oce.reconfigure()
    oce.acquire_request(None)
    iast_start_request()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()


IAST_ENV = {"DD_IAST_ENABLED": "true", "DD_IAST_REQUEST_SAMPLING": "100", "DD_IAST_MAX_CONCURRENT_REQUEST": "100"}


@contextlib.contextmanager
def _with_iast_context():
    with override_env(IAST_ENV):
        asm_config._iast_enabled = True
        asm_config._iast_propagation_enabled = True
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


@contextlib.contextmanager
def _without_iast_context():
    with override_env({"DD_IAST_ENABLED": "false"}):
        asm_config._iast_enabled = False
        asm_config._iast_propagation_enabled = False
        yield
