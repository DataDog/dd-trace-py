from collections import deque
from functools import partial
from functools import singledispatch
from pathlib import Path
from types import CodeType
from types import FunctionType
from typing import Set
from typing import cast

from bytecode import Bytecode

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY
from ddtrace.internal.safety import _isinstance


@singledispatch
def linenos(_) -> Set[int]:
    raise NotImplementedError()


@linenos.register
def _(code: CodeType) -> Set[int]:
    """Get the line numbers of a function."""
    if PY >= (3, 10):
        return {_ for _ in (_[2] for _ in code.co_lines()) if _ is not None} - {code.co_firstlineno}

    return {
        _ for _ in (instr.lineno for instr in Bytecode.from_code(code) if hasattr(instr, "lineno")) if _ is not None
    }


@linenos.register
def _(f: FunctionType) -> Set[int]:
    return linenos(f.__code__)


def undecorated(f: FunctionType, name: str, path: Path) -> FunctionType:
    # Find the original function object from a decorated function. We use the
    # expected function name to guide the search and pick the correct function.
    # The recursion is needed in case of multiple decorators. We make it BFS
    # to find the function as soon as possible.

    def match(g):
        return g.__code__.co_name == name and Path(g.__code__.co_filename).resolve() == path

    if _isinstance(f, FunctionType) and match(f):
        return f

    seen_functions = {f}
    q = deque([f])  # FIFO: use popleft and append

    while q:
        g = q.popleft()

        # Look for a wrapped function. These attributes are generally used by
        # the decorators provided by the standard library (e.g. partial)
        for attr in ("__wrapped__", "func"):
            try:
                wrapped = object.__getattribute__(g, attr)
                if _isinstance(wrapped, FunctionType) and wrapped not in seen_functions:
                    if match(wrapped):
                        return wrapped
                    q.append(wrapped)
                    seen_functions.add(wrapped)
            except AttributeError:
                pass

        # A partial object is a common decorator. The function can either be the
        # curried function, or it can appear as one of the arguments (e.g. the
        # implementation of the wraps decorator).
        if _isinstance(g, partial):
            p = cast(partial, g)
            if match(p.func):
                return cast(FunctionType, p.func)
            for arg in p.args:
                if _isinstance(arg, FunctionType) and arg not in seen_functions:
                    if match(arg):
                        return arg
                    q.append(arg)
                    seen_functions.add(arg)
            for arg in p.keywords.values():
                if _isinstance(arg, FunctionType) and arg not in seen_functions:
                    if match(arg):
                        return arg
                    q.append(arg)
                    seen_functions.add(arg)

        # Look for a closure (function decoration)
        if _isinstance(g, FunctionType):
            for c in (_.cell_contents for _ in (g.__closure__ or []) if _isinstance(_.cell_contents, FunctionType)):
                if c not in seen_functions:
                    if match(c):
                        return c
                    q.append(c)
                    seen_functions.add(c)

        # Look for a function attribute (method decoration)
        # DEV: We don't recurse over arbitrary objects. We stop at the first
        # depth level.
        try:
            for v in object.__getattribute__(g, "__dict__").values():
                if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                    return v
        except AttributeError:
            # Maybe we have slots
            try:
                for v in (object.__getattribute__(g, _) for _ in object.__getattribute__(g, "__slots__")):
                    if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                        return v
            except AttributeError:
                pass

        # Last resort
        try:
            for v in (object.__getattribute__(g, a) for a in object.__dir__(g)):
                if _isinstance(v, FunctionType) and v not in seen_functions and match(v):
                    return v
        except AttributeError:
            pass

    return f
