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
    module_coverage_hook: t.Optional[HookType],
    module_dependency_hook: t.Optional[HookType],
) -> t.Tuple[CodeType, t.Set[int]]:
    """Installs the relevant hook to collect coverage data for all lines in the code object.

    More advance module-level coverage exists to capture module-level imports and dependencies (if desired). To avoid
    the overhead of installing and calling multiple hooks per line, but keep the most-exercised line-level hook, as
    simple as possible, we selectively install the module-level hooks only when needed.
    """
    abstract_code = Bytecode.from_code(code)
    final_abstract_code = Bytecode.from_code(code)
    final_abstract_code.clear()

    lines = set()

    is_module = code.co_name == "<module>"
    instrument_module = is_module and module_coverage_hook is not None and module_dependency_hook is not None

    last_lineno = None
    current_module_line_instrs = []
    current_module_line_is_instrumented = False

    for i, instr in enumerate(abstract_code):
        try:
            print(f"CURRENT {instr=}, {last_lineno=}, {is_module=}")
            # Keep track of the index of the first instruction in a line as a place to insert instrumentation hooks
            if is_module and instrument_module:
                # Never drop instructions from the current line
                current_module_line_instrs.append(instr)

                # Collect module-level imports to be able to rebuild module dependencies when computing coverage
                # Note that, for module-level imports, all instructions need to be processed (since the import is not
                # the only instruction on the line)
                if module_dependency_hook is not None and isinstance(instr, Instr) and instr.name == "IMPORT_NAME":
                    final_abstract_code.extend(INJECTION_ASSEMBLY.bind(
                        dict(hook=module_dependency_hook, arg=(path, instr.lineno, instr.arg), lineno=instr.lineno)
                    ))
                    current_module_line_is_instrumented = True

                # Only install the module-level hook at the end of the line, or end of code
                next_instr = abstract_code[i + 1] if i < len(abstract_code) -1 else None
                if next_instr is None or (isinstance(next_instr, Instr) and next_instr.lineno is not None and next_instr.lineno != last_lineno):
                    # For module-level lines, we can only install the hook at the end of the line (ie: if the next
                    # instruction is on a different line)
                    if not current_module_line_is_instrumented:
                        final_abstract_code.extend(INJECTION_ASSEMBLY.bind(
                        dict(hook=module_coverage_hook, arg=(path, last_lineno)), lineno=last_lineno
                    ))
                        current_module_line_is_instrumented = True
                        lines.add(last_lineno)
                    final_abstract_code.extend(current_module_line_instrs)
                    # Reset state once a current line's been flushed
                    current_module_line_is_instrumented = False
                    current_module_line_instrs = []
            else:
                # Only inject the hook at the beginning of the line if we have not already done so
                if isinstance(instr, Instr) and instr.lineno is not None and instr.lineno != last_lineno:
                    print(f"INJECTING AT LINE {instr.lineno}")
                    to_inject = INJECTION_ASSEMBLY.bind(
                        dict(hook=line_coverage_hook, arg=(path, instr.lineno), lineno=instr.lineno)
                    )
                    print(f"TO INJECT {to_inject=}")
                    final_abstract_code.extend(to_inject)
                    lines.add(instr.lineno)

                # Append the original instruction
                final_abstract_code.append(instr)
        except AttributeError:
            pass

        if isinstance(instr, Instr) and instr.lineno is not None:
            last_lineno = instr.lineno

    import dis
    print("ORIG CODE")
    dis.dis(abstract_code.to_code())
    print("NEW CODE")
    final_code = final_abstract_code.to_code()
    dis.dis(final_code)
    breakpoint()

    return final_code, lines
