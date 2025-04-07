from typing import Any

import bm

from ddtrace.appsec._iast._iast_request_context import end_iast_context
from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context import start_iast_context
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect


TAINT_ORIGIN = Source(name="sample_name", value="sample_value", origin=OriginType.PARAMETER)

CHECK_RANGES = [TaintRange(0, 3, TAINT_ORIGIN), TaintRange(21, 3, TAINT_ORIGIN), TaintRange(41, 3, TAINT_ORIGIN)]


def _start_iast_context_and_oce():
    oce.reconfigure()
    oce.acquire_request(None)
    start_iast_context()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()
    oce.release_request()


def taint_pyobject_with_ranges(pyobject: Any, ranges: tuple) -> None:
    set_ranges(pyobject, tuple(ranges))


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
    tainted = b"my_string".decode("ascii")

    if enable_propagation:
        taint_pyobject_with_ranges(tainted, (CHECK_RANGES[0],))
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
