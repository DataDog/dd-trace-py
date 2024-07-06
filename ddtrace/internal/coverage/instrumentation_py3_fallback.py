from types import CodeType
import typing as t

from bytecode import Bytecode

from ddtrace.internal.injection import INJECTION_ASSEMBLY
from ddtrace.internal.injection import HookType


def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
    abstract_code = Bytecode.from_code(code)

    lines = set()

    last_lineno = None
    for i, instr in enumerate(abstract_code):
        try:
            # Recursively instrument nested code objects
            if isinstance(instr.arg, CodeType):
                instr.arg, nested_lines = instrument_all_lines(instr.arg, hook, path)
                lines.update(nested_lines)

            if instr.lineno == last_lineno:
                continue

            last_lineno = instr.lineno
            if last_lineno is None:
                continue

            if instr.name == "NOP":
                continue

            # Inject the hook at the beginning of the line
            abstract_code[i:i] = INJECTION_ASSEMBLY.bind(dict(hook=hook, arg=(last_lineno, path)), lineno=last_lineno)

            # Track the line number
            lines.add(last_lineno)
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass

    to_code = abstract_code.to_code()

    return to_code, lines
