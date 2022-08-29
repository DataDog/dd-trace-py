from types import FunctionType
from typing import Set

from bytecode import Bytecode


def linenos(f):
    # type: (FunctionType) -> Set[int]
    """Get the line numbers of a function."""
    return {instr.lineno for instr in Bytecode.from_code(f.__code__) if hasattr(instr, "lineno")}
