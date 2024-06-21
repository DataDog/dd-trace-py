from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.injection import INJECTION_ASSEMBLY
from ddtrace.internal.injection import HookType


def instrument_all_lines(
    code: CodeType,
    hook: HookType,
    path: str,
    module_dependency_hook: t.Optional[HookType],
) -> t.Tuple[CodeType, t.Set[int]]:
    abstract_code = Bytecode.from_code(code)
    lines = set()

    is_module = code.co_name == "<module>"
    instrument_imports = module_dependency_hook is not None and is_module

    line_start_instr_index = 0
    import_instrumented_lines = set()

    last_lineno = None
    for i, instr in enumerate(abstract_code):
        if not isinstance(instr, Instr):
            continue

        try:
            if instr.lineno is None:
                continue

            # Collect module-level imports to be able to rebuild module dependencies when computing coverage
            # Note that, for module-level imports, all instructions need to be processed (since the import is not the
            # only instruction on the line)
            if instrument_imports and instr.lineno not in import_instrumented_lines:
                if instr.name == "IMPORT_NAME":
                    abstract_code[line_start_instr_index + 4 : line_start_instr_index + 4] = INJECTION_ASSEMBLY.bind(
                        dict(hook=module_dependency_hook, arg=(path, instr.arg), lineno=last_lineno)
                    )
                    import_instrumented_lines.add(instr.lineno)

            if instr.lineno == last_lineno:
                continue
            last_lineno = instr.lineno

            # Keep track of the index of the first instruction in a line as a place to insert instrumentation hooks
            line_start_instr_index = i

            if instr.name == "RESUME":
                continue

            # Only inject the hook at the beginning of the line if we have not already done so
            abstract_code[line_start_instr_index:line_start_instr_index] = INJECTION_ASSEMBLY.bind(
                dict(hook=hook, arg=(path, last_lineno, is_module)), lineno=last_lineno
            )

            # Track the line number
            lines.add(last_lineno)
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass

    return abstract_code.to_code(), lines
