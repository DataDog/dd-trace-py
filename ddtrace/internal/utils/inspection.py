from collections import deque
from functools import singledispatch
from pathlib import Path
from types import CodeType
from types import FunctionType
from typing import Set

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
    # DEV: We are deliberately not handling decorators that store the original
    #      function in __wrapped__ for now.
    path = path.resolve()

    if not (f.__code__.co_name == name and Path(f.__code__.co_filename).resolve() == path):
        seen_functions = {f}
        q = deque([f])  # FIFO: use popleft and append

        while q:
            next_f = q.popleft()

            for g in (
                _.cell_contents for _ in (next_f.__closure__ or []) if _isinstance(_.cell_contents, FunctionType)
            ):
                if g.__code__.co_name == name and Path(g.__code__.co_filename).resolve() == path:
                    return g
                if g not in seen_functions:
                    q.append(g)
                    seen_functions.add(g)

    return f
