import contextvars
import uuid
import time
import io
from contextlib import redirect_stdout

from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
from ddtrace.internal import core
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._iast_env import IASTEnvironment



def create_con_new():
    IAST_CONTEXT.set(str(uuid.uuid4()))

def get_con_new():
    print(IAST_CONTEXT.get())

def create_con():
    core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment())

def get_con():
    env = core.get_item(IAST.REQUEST_CONTEXT_KEY)
    print(env)

if __name__ == "__main__":
    # Initialize both contexts once
    create_con()
    create_con_new()

    iterations = 100_000
    sink = io.StringIO()  # suppress printing cost during timing

    with redirect_stdout(sink):
        t0 = time.perf_counter()
        for _ in range(iterations):
            get_con()
        t1 = time.perf_counter()

    with redirect_stdout(sink):
        t2 = time.perf_counter()
        for _ in range(iterations):
            get_con_new()
        t3 = time.perf_counter()

    with redirect_stdout(sink):
        t4 = time.perf_counter()
        for _ in range(iterations):
            get_con_new()
        t5 = time.perf_counter()

    dd_core_time = t1 - t0
    ctxvar_time = t3 - t2
    ctxvar_cpp_time = t5 - t4

    print(f"Iterations: {iterations}")
    print(f"get_con (ddtrace.core): {dd_core_time:.6f}s")
    print(f"get_con_new (contextvar): {ctxvar_time:.6f}s")
    print(f"get_con_new (ctxvar from C++): {ctxvar_cpp_time:.6f}s")
    if ctxvar_time > 0:
        print(f"Speedup (core/get_con_new): {dd_core_time/ctxvar_time:.2f}x")
        print(f"Speedup (core/ctxvar_cpp_time): {dd_core_time/ctxvar_cpp_time:.2f}x")