import bm


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

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect


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


def normal_function(internal_loop, tainted):
    value = ""
    res = value
    for _ in range(internal_loop):
        res += "_".join((tainted, "_", tainted))
        value = res
        res += tainted
        value = res
        res += " "
        value = res
    return value


def aspect_function(internal_loop, tainted):
    value = ""
    res = value
    for _ in range(internal_loop):
        res = add_aspect(res, join_aspect("_".join, 1, "_", (tainted, "_", tainted)))
        value = res
        res = add_aspect(res, tainted)
        value = res
        res = add_aspect(res, " ")
        value = res

    return value


def new_request(enable_propagation):
    tainted = "my_string"

    if enable_propagation:
        tainted = taint_pyobject(tainted, source_name="path", source_value=tainted, source_origin=OriginType.PATH)
    return tainted


def launch_function(enable_propagation, func, internal_loop, caller_loop):
    for _ in range(caller_loop):
        tainted_value = new_request(enable_propagation)
        func(internal_loop, tainted_value)


class IastPropagation(bm.Scenario):
    iast_enabled: int
    internal_loop: int

    def run(self):
        caller_loop = 10
        if self.iast_enabled:
            _start_iast_context_and_oce()
            func = aspect_function
        else:
            func = normal_function

        def _(loops):
            for _ in range(loops):
                launch_function(self.iast_enabled, func, self.internal_loop, caller_loop)

        yield _
        if self.iast_enabled:
            _end_iast_context_and_oce()
