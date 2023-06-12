from types import FunctionType
from typing import Set

from bytecode import Bytecode

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY


def linenos(f):
    # type: (FunctionType) -> Set[int]
    """Get the line numbers of a function."""
    ls = {instr.lineno for instr in Bytecode.from_code(f.__code__) if hasattr(instr, "lineno")}
    if PY >= (3, 11):
        # Python 3.11 has introduced some no-op instructions that are used as
        # part of the specialisation process. These new opcodes appear on a
        # fictitious line number that generally corresponds to the line where
        # the function is defined. We remove this line number from the set of
        # line numbers to avoid reporting it as part of the function's source
        ls.remove(f.__code__.co_firstlineno)
    return ls
