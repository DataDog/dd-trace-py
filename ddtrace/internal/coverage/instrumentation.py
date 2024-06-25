from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr, Label

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

    lines = set()

    is_module = collect_module_dependencies and code.co_name == "<module>"

    previous_line_import: t.Optional[str] = None

    previous_line_instrs = []
    previous_line_number = None

    # Code must be instrumented line-by-line to capture import statements
    for i in range(len(abstract_code) + 1):
        instr = abstract_code[i] if i < len(abstract_code) else None

        # Always appended to the previous line, and move on:
        # - Instructions that are not Instr instances (eg: labels)
        # - Instructions that have line numbers set to None
        if instr is not None and (not isinstance(instr, Instr) or instr.lineno is None):
            previous_line_instrs.append(instr)
            continue

        # If the current instruction is on a different line, or if the current instruction is the last one in the
        # code object, instrument and flush the previous line
        if instr is None or instr.lineno != previous_line_number:
            # Don't inject the hook if the previous line was empty
            if previous_line_number is not None and previous_line_instrs:
                to_inject = INJECTION_ASSEMBLY.bind(
                        dict(hook=line_coverage_hook, arg=(path, previous_line_number, is_module, previous_line_import )), lineno=previous_line_number
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



        # # Keep track of the index of the first instruction in a line as a place to insert instrumentation hooks
        # if instrument_module:
        #     # Never drop instructions from the current line
        #     current_module_line_instrs.append(instr)
        #
        #     # Collect module-level imports to be able to rebuild module dependencies when computing coverage
        #     # Note that, for module-level imports, all instructions need to be processed (since the import is not
        #     # the only instruction on the line)
        #     if isinstance(instr, Instr) and instr.name == "IMPORT_NAME":
        #         current_module_line_import = instr.arg
        #
        #     # Only install the module-level hook at the end of the line, or end of code
        #     next_instr = abstract_code[i + 1] if i < len(abstract_code) -1 else None
        #     if next_instr is None or (isinstance(next_instr, Instr) and next_instr.lineno is not None and next_instr.lineno != instr.lineno) and last_lineno is not None and instr.lineno not in lines:
        #         if code.co_filename == "/Users/romain.komorn/nondd/flask-shallow/src/flask/cli.py" and instr.lineno in [1110, 1111]:
        #             import dis
        #             dis.dis(code, depth=0)
        #             breakpoint()
        #         # For module-level lines, we can only install the hook at the end of the line (ie: if the next
        #         # instruction is on a different line)
        #         instrumented_abstract_code.extend(INJECTION_ASSEMBLY.bind(
        #             dict(hook=module_coverage_hook, arg=(path, instr.lineno,current_module_line_import )), lineno=instr.lineno
        #         ))
        #         instrumented_abstract_code.extend(current_module_line_instrs)
        #         lines.add(last_lineno)
        #         # Reset state once a current line's been flushed
        #         current_module_line_instrs = []
        #         current_module_line_import = None
        #         last_lineno = instr.lineno
        # else:
        #     # Only inject the hook at the beginning of the line if we have not already done so
        #     if isinstance(instr, Instr) and instr.lineno is not None and instr.lineno != last_lineno:
        #         to_inject = INJECTION_ASSEMBLY.bind(
        #             dict(hook=line_coverage_hook, arg=(path, instr.lineno)), lineno=instr.lineno
        #         )
        #         instrumented_abstract_code.extend(to_inject)
        #         lines.add(instr.lineno)
        #
        #     # Append the original instruction
        #     instrumented_abstract_code.append(instr)

    # if isinstance(instr, Instr) and instr.lineno is not None:
    #     last_lineno = instr.lineno

    # import dis
    # print("ORIG CODE")
    # dis.dis(abstract_code.to_code(), depth=0)
    # print("NEW CODE")
    # final_code = instrumented_abstract_code.to_code()
    # dis.dis(final_code, depth=0)

    # if code.co_filename == "/Users/romain.komorn/nondd/flask-shallow/src/flask/cli.py":
    #     import dis
    #     print("ORIG")
    #     print(abstract_code)
    #     dis.dis(code, depth=0)
    #     print("NEW")
    #     print(instrumented_abstract_code)
    #     dis.dis(final_code, depth=0)

    return instrumented_abstract_code.to_code(), lines
