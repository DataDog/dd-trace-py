from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.injection import INJECTION_ASSEMBLY
from ddtrace.internal.injection import HookType


def instrument_all_lines(
    code: CodeType,
    line_coverage_hook: HookType,
    path: str,
    collect_module_dependencies: bool = False,
) -> t.Tuple[CodeType, t.Set[int]]:
    """Installs the relevant hook to collect coverage data for all lines in the code object.

    More advance module-level coverage exists to capture module-level imports and dependencies (if desired). To avoid
    the overhead of installing and calling multiple hooks per line, but keep the most-exercised line-level hook, as
    simple as possible, we selectively install the module-level hooks only when needed.
    """
    abstract_code = Bytecode.from_code(code)
    instrumented_abstract_code = Bytecode.from_code(code)
    instrumented_abstract_code.clear()

    lines: t.Set[int] = set()

    is_module = collect_module_dependencies and code.co_name == "<module>"

    previous_line_import: t.Optional[str] = None
    previous_line_instrs = []
    previous_line_number = None

    # Code must be instrumented line-by-line to capture import statements
    # This loop goes past the end of the code to ensure the last line is instrumented
    for i in range(len(abstract_code) + 1):
        # Get the current instruction, or None if we've reached the end of the code
        instr = abstract_code[i] if i < len(abstract_code) else None

        # Always appended to the previous line, and move on:
        # - Instructions that are not Instr instances (eg: labels)
        # - Instructions that have line numbers set to None
        if instr is not None:
            if not isinstance(instr, Instr) or instr.lineno is None:
                previous_line_instrs.append(instr)
                continue
            if instr.name == "RESUME":
                instrumented_abstract_code.append(instr)
                continue

        # If the current instruction is on a different line, or if the current instruction is the last one in the
        # code object, instrument and flush the previous line
        if instr is None or instr.lineno != previous_line_number:
            # Don't inject the hook if the previous line was empty
            if previous_line_instrs and previous_line_number is not None:
                to_inject = INJECTION_ASSEMBLY.bind(
                    dict(hook=line_coverage_hook, arg=(path, previous_line_number, is_module, previous_line_import)),
                    lineno=previous_line_number,
                )
                instrumented_abstract_code.extend(to_inject)
                instrumented_abstract_code.extend(previous_line_instrs)
                lines.add(previous_line_number)

            # Exit out of the loop if we've reached the end of the code
            if instr is None:
                break

            # Now that the previous line has been flushed, reset the state
            previous_line_instrs = []
            previous_line_import = None
            previous_line_number = instr.lineno

        previous_line_instrs.append(instr)

        if not isinstance(instr, Instr):
            continue

        if instr.name == "IMPORT_NAME" and collect_module_dependencies:
            previous_line_import = instr.arg

    return instrumented_abstract_code.to_code(), lines
