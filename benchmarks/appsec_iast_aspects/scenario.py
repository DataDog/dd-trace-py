import contextlib

import bm
from bm.utils import override_env

from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_tainted
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


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


with override_env(IAST_ENV):
    asm_config._iast_enabled = True
    asm_config._iast_propagation_enabled = True
    enable_iast_propagation()

import functions  # noqa: E402


class IASTAspects(bm.Scenario):
    iast_enabled: bool
    function_name: str

    def run(self):
        # Create a tainted parameter to pass to functions
        tainted_param = "example_string"

        def aspect_loop(loops):
            for _ in range(loops):
                if self.iast_enabled:
                    # Taint the parameter for each iteration
                    tainted = taint_pyobject(
                        tainted_param,
                        source_name="path2",
                        source_value=tainted_param,
                        source_origin=OriginType.PARAMETER,
                    )
                    result = getattr(functions, self.function_name)(tainted)
                    assert is_tainted(result)
                else:
                    _ = getattr(functions, self.function_name)(tainted_param)

        context = _with_iast_context if self.iast_enabled else _without_iast_context
        with context():
            yield aspect_loop
            if self.iast_enabled:
                num_tainted = _num_objects_tainted_in_request()
                context_size = debug_context_array_size()
                free_contexts = debug_context_array_free_slots_number()
                assert num_tainted > 0
                assert context_size == 2
                assert free_contexts > 0
