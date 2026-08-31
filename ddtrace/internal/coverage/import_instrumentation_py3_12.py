"""Import dependency bytecode helpers for Python 3.12+ coverage instrumentation."""

import sys
from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.bytecode_injection import HookType


# INJECTION_ASSEMBLY is the bytecode-rewriting Assembly used by inject_import_hooks() to splice
# `hook(arg); pop_top` after IMPORT_NAME/IMPORT_FROM opcodes. On Python 3.15+ the bytecode_injection
# module switched to a sys.monitoring-based implementation and no longer exposes INJECTION_ASSEMBLY.
# Accurate import-hook injection is correspondingly disabled on 3.15+ (see
# instrumentation_py3_12._USE_ACCURATE_IMPORTS), so inject_import_hooks() is never called there
# and INJECTION_ASSEMBLY is unused. The import is gated so this module can still be imported on
# 3.15+; otherwise merely loading the coverage backend would raise ImportError.
if sys.version_info < (3, 15):
    from ddtrace.internal.bytecode_injection import INJECTION_ASSEMBLY
else:
    INJECTION_ASSEMBLY = None  # type: ignore[assignment]


ImportName = tuple[str, tuple[str, ...]]
ImportNamesByLine = dict[int, ImportName]
CoverageHookArg = tuple[int, str, ImportName]
PendingImportHook = tuple[int, CoverageHookArg, int]


class ImportEvent(t.NamedTuple):
    instruction_index: int
    line: int
    import_name: ImportName


def _resolve_import_package(package: str, import_depth: int) -> str:
    return ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package


def _decoded_import_depth(value: t.Any) -> int:
    return value if isinstance(value, int) else 0


def _decoded_arg_for_history(instr: Instr) -> t.Any:
    if instr.name == "LOAD_CONST":
        return instr.arg
    if instr.name == "LOAD_SMALL_INT":
        return instr.arg if isinstance(instr.arg, int) else 0
    return 0


def iter_import_events(
    code_or_bytecode: CodeType | Bytecode, package: str, code: t.Optional[CodeType] = None
) -> list[ImportEvent]:
    """Return import bytecode events in execution order.

    The returned metadata is shared by line-level static import tracking and file-level import-hook injection so import
    decoding semantics stay in one place.
    """
    if isinstance(code_or_bytecode, CodeType):
        code = code_or_bytecode
        bytecode = Bytecode.from_code(code)
    else:
        bytecode = code_or_bytecode
        if code is None:
            raise ValueError("code must be provided when code_or_bytecode is a Bytecode object")

    events: list[ImportEvent] = []
    current_import_name: t.Optional[str] = None
    current_import_package = package
    previous_previous_arg: t.Any = 0
    previous_arg: t.Any = 0

    for idx, instr in enumerate(bytecode):
        if not isinstance(instr, Instr):
            continue

        lineno = instr.lineno or code.co_firstlineno
        if instr.name == "IMPORT_NAME":
            import_depth = _decoded_import_depth(previous_previous_arg)
            # AIDEV-TODO: On Python 3.15 (PEP 810 lazy imports) the `bytecode` library decodes
            # IMPORT_NAME's bit-packed arg into a (lazy, eager, name) tuple rather than a plain
            # name string, so this cast is wrong on 3.15 and import_names_by_line/inject_import_hooks
            # would misbehave. This is currently unreachable on 3.15+ because accurate import
            # injection is disabled there (see instrumentation_py3_12._USE_ACCURATE_IMPORTS), but it
            # must be fixed before re-enabling accurate imports on 3.15+.
            current_import_name = t.cast(str, instr.arg)
            current_import_package = _resolve_import_package(package, import_depth)
            events.append(ImportEvent(idx, lineno, (current_import_package, (current_import_name,))))
        elif instr.name == "IMPORT_FROM" and current_import_name is not None:
            import_from_name = f"{current_import_name}.{instr.arg}"
            events.append(ImportEvent(idx, lineno, (current_import_package, (import_from_name,))))

        previous_previous_arg = previous_arg
        previous_arg = _decoded_arg_for_history(instr)

    return events


def import_names_by_line(import_events: t.Iterable[ImportEvent]) -> ImportNamesByLine:
    import_names: ImportNamesByLine = {}
    for event in import_events:
        package, modules = event.import_name
        if event.line in import_names:
            previous_package, previous_modules = import_names[event.line]
            import_names[event.line] = (package or previous_package, previous_modules + modules)
        else:
            import_names[event.line] = (package, modules)
    return import_names


def inject_import_hooks(
    code_or_bytecode: CodeType | Bytecode, hook: HookType, path: str, import_events: t.Iterable[ImportEvent]
) -> CodeType:
    """Inject import dependency hooks immediately after actual import bytecodes.

    File-level coverage uses PY_START, which is too early to know whether guarded imports in the code object will run.
    These injected hooks fire only when the interpreter reaches IMPORT_NAME/IMPORT_FROM, so false runtime branches do
    not create dependency edges. The hook is inserted after the import opcode, meaning failed imports are not recorded.
    """
    if isinstance(code_or_bytecode, CodeType):
        bytecode = Bytecode.from_code(code_or_bytecode)
    else:
        bytecode = code_or_bytecode

    if INJECTION_ASSEMBLY is None:
        # Accurate import-hook injection relies on bytecode rewriting that is not available on
        # Python 3.15+. This path is unreachable in normal operation because instrumentation_py3_12
        # gates accurate imports behind _USE_ACCURATE_IMPORTS (False on 3.15+); raise explicitly so
        # any future caller fails loudly instead of hitting an AttributeError on None.bind().
        raise RuntimeError("inject_import_hooks is not supported on Python 3.15+")

    pending_insertions: list[PendingImportHook] = [
        (event.instruction_index, (0, path, event.import_name), event.line) for event in import_events
    ]

    for idx, arg, lineno in reversed(pending_insertions):
        bytecode[idx + 1 : idx + 1] = INJECTION_ASSEMBLY.bind(dict(hook=hook, arg=arg), lineno=lineno)

    return bytecode.to_code()
