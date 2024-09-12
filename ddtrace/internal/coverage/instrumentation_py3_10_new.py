from abc import ABC
import dis
from enum import Enum
import sys
from types import CodeType
import typing as t

#from ddtrace.internal.coverage.lines import CoverageLines
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.injection import HookType


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10) and sys.version_info < (3, 11)  # nosec



EXTENDED_ARG = dis.EXTENDED_ARG
NO_OFFSET = -1


LOAD_CONST = dis.opmap["LOAD_CONST"]
CALL = dis.opmap["CALL_FUNCTION"]
POP_TOP = dis.opmap["POP_TOP"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]


# def trap_call(trap_index: int, arg_index: int) -> t.Tuple[Instruction, ...]:
#     return (
#         *instr_with_arg(LOAD_CONST, trap_index),
#         *instr_with_arg(LOAD_CONST, arg_index),
#         Instruction(NO_OFFSET, CALL, 1),
#         Instruction(NO_OFFSET, POP_TOP, 0),
#     )

JUMPS = set(dis.hasjabs + dis.hasjrel)

ABSOLUTE_JUMPS = set(dis.hasjabs)
BACKWARD_JUMPS = set(op for op in dis.hasjrel if "BACKWARD" in dis.opname[op])
FORWARD_JUMPS = set(op for op in dis.hasjrel if "BACKWARD" not in dis.opname[op])


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    extended_arg = 0
    old_code = code.co_code
    new_code = bytearray()
    new_linetable = bytearray()

    previous_line = code.co_firstlineno
    previous_line_new_offset = 0
    previous_previous_line = 0

    arg: int = 0
    previous_arg: int = 0
    previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    new_offsets = {} # [None] * len(old_code)
    new_ends = {}
    old_targets = {} # [None] * len(old_code)
    line_starts = dict(dis.findlinestarts(code))

    new_consts = list(code.co_consts)
    trap_index = len(new_consts)
    new_consts.append(hook)

    seen_lines = CoverageLines()


    def update_line(offset_delta, line_delta):
        while offset_delta > 254:
            new_linetable.append(254)  # 254 offsets with no line change
            new_linetable.append(0)    #
            offset_delta -= 254

        new_linetable.append(offset_delta)

        while line_delta > 127:
            new_linetable.append(127) # line_delta
            new_linetable.append(0)   # offset_delta
            line_delta -= 127

        while line_delta < -127:
            new_linetable.append(0x81) # line_delta
            new_linetable.append(0)    # offset_table
            line_delta += 127

        new_linetable.append(line_delta & 0xFF)

    def append_instruction(op, arg):
        new_code.append(op)
        new_code.append(arg)


    def append_instruction_ext(op, arg):
        new_offset = len(new_code)
        new_code.append(op)
        new_code.append(arg & 0xFF)
        arg >>= 8
        while arg:
            # insert extended args BEFORE op
            new_code.insert(new_offset, EXTENDED_ARG)
            new_code.insert(new_offset+1, arg & 0xFF)
            arg >>= 8


    def trap_call():
        append_instruction_ext(LOAD_CONST, trap_index)
        append_instruction_ext(LOAD_CONST, len(new_consts))
        append_instruction(CALL, 1)
        append_instruction(POP_TOP, 0)


    for old_offset in range(0, len(old_code), 2):
        op = old_code[old_offset]
        previous_previous_arg = previous_arg
        previous_arg = arg
        arg = old_code[old_offset+1] | extended_arg

        new_offset = len(new_code)
        new_offsets[old_offset] = new_offset

        line = line_starts.get(old_offset)
        if line is not None:
            if old_offset > 0:
                update_line(new_offset - previous_line_new_offset, previous_line - previous_previous_line)
            previous_previous_line = previous_line
            previous_line_new_offset = new_offset
            previous_line = line

            seen_lines.add(line)

            trap_call()
            # Make sure that the current module is marked as depending on its own package by instrumenting the
            # first executable line
            package_dep = None
            if code.co_name == "<module>" and len(new_consts) == len(code.co_consts) + 1:
                package_dep = (package, ("",))
            new_consts.append((line, path, package_dep))


        if op == EXTENDED_ARG:
            extended_arg = arg << 8
            continue

        extended_arg = 0


        if op in JUMPS:
            if op in ABSOLUTE_JUMPS:
                target = arg * 2
            elif op in FORWARD_JUMPS:
                target = old_offset + 2 + (arg * 2)
            elif op in BACKWARD_JUMPS:
                target = old_offset + 2 - (arg * 2)
            else:
                raise NotImplementedError("oops")

            old_targets[old_offset] = target

            new_code.append(EXTENDED_ARG)
            new_code.append(0)  # placeholder
            new_code.append(EXTENDED_ARG)
            new_code.append(0)  # placeholder
            new_code.append(EXTENDED_ARG)
            new_code.append(0)  # placeholder
            new_code.append(op)
            new_code.append(0)  # placeholder

            new_ends[old_offset] = len(new_code)

        else:
            append_instruction_ext(op, arg)

            # Track imports names
            if op == IMPORT_NAME:
                import_depth = code.co_consts[previous_previous_arg]
                current_import_name = code.co_names[arg]
                # Adjust package name if the import is relative and a parent (ie: if depth is more than 1)
                current_import_package = (
                    ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package
                )
                new_consts[-1] = (
                    new_consts[-1][0],
                    new_consts[-1][1],
                    (current_import_package, (current_import_name,)),
                )

            # Also track import from statements since it's possible that the "from" target is a module, eg:
            # from my_package import my_module
            # Since the package has not changed, we simply extend the previous import names with the new value
            if op == IMPORT_FROM:
                import_from_name = f"{current_import_name}.{code.co_names[arg]}"
                new_consts[-1] = (
                    new_consts[-1][0],
                    new_consts[-1][1],
                    (new_consts[-1][2][0], tuple(list(new_consts[-1][2][1]) + [import_from_name])),
                )


    update_line(len(new_code) - new_offset, previous_line - previous_previous_line)

    # Fixup the offsets.
    for old_offset, old_target in old_targets.items():
        new_offset = new_offsets[old_offset]
        new_target = new_offsets[old_target]
        new_end = new_ends[old_offset]
        op = old_code[old_offset]
        if op in ABSOLUTE_JUMPS:
            arg = new_target // 2
        elif op in FORWARD_JUMPS:
            arg = (new_target - new_end) // 2
        elif op in BACKWARD_JUMPS:
            arg = (new_end - new_target) // 2
        else:
            #breakpoint()
            raise NotImplementedError("oops")

        # if op == dis.opmap['SETUP_FINALLY']:
        #     breakpoint()

        new_code[new_end - 7] = (arg >> 24) & 0xFF
        new_code[new_end - 5] = (arg >> 16) & 0xFF
        new_code[new_end - 3] = (arg >>  8) & 0xFF
        new_code[new_end - 1] = arg         & 0xFF


    # Instrument nested code objects recursively
    for original_offset, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType):
            new_consts[original_offset], nested_lines = instrument_all_lines(nested_code, hook, path, package)
            seen_lines.update(nested_lines)


    return code.replace(
        co_code=bytes(new_code),
        co_consts=tuple(new_consts),
        co_linetable=bytes(new_linetable),
        co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
    ), seen_lines
