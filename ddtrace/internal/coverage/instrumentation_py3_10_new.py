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


class JumpDirection(int, Enum):
    FORWARD = 1
    BACKWARD = -1

    @classmethod
    def from_opcode(cls, opcode: int) -> "JumpDirection":
        return cls.BACKWARD if "BACKWARD" in dis.opname[opcode] else cls.FORWARD


class Jump(ABC):
    def __init__(self, start: int, arg: int) -> None:
        self.start = start
        self.end: int
        self.arg = arg


class AJump(Jump):
    __opcodes__ = set(dis.hasjabs)

    def __init__(self, start: int, arg: int) -> None:
        super().__init__(start, arg)
        self.end = self.arg << 1


class RJump(Jump):
    __opcodes__ = set(dis.hasjrel)

    def __init__(self, start: int, arg: int, direction: JumpDirection) -> None:
        super().__init__(start, arg)
        self.direction = direction
        self.end = start + (self.arg << 1) * self.direction + 2


class Instruction:
    __slots__ = ("offset", "opcode", "arg", "targets")

    def __init__(self, offset: int, opcode: int, arg: int) -> None:
        self.offset = offset
        self.opcode = opcode
        self.arg = arg
        self.targets: t.List["Branch"] = []


class Branch(ABC):
    def __init__(self, start: Instruction, end: Instruction) -> None:
        self.start = start
        self.end = end

    @property
    def arg(self) -> int:
        raise NotImplementedError


class RBranch(Branch):
    @property
    def arg(self) -> int:
        return abs(self.end.offset - self.start.offset - 2) >> 1


class ABranch(Branch):
    @property
    def arg(self) -> int:
        return self.end.offset >> 1


EXTENDED_ARG = dis.EXTENDED_ARG
NO_OFFSET = -1


def instr_with_arg(opcode: int, arg: int) -> t.List[Instruction]:
    instructions = [Instruction(NO_OFFSET, opcode, arg & 0xFF)]
    arg >>= 8
    while arg:
        instructions.insert(0, Instruction(NO_OFFSET, EXTENDED_ARG, arg & 0xFF))
        arg >>= 8
    return instructions


def update_location_data(
    code: CodeType, trap_map: t.Dict[int, int], ext_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    # DEV: We expect the original offsets in the trap_map
    new_data = bytearray()

    data = code.co_linetable
    data_iter = iter(data)
    ext_arg_offset_iter = iter(sorted(ext_arg_offsets))
    ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))

    original_offset = offset = 0
    while True:
        try:
            if original_offset in trap_map:
                # Give no line number to the trap instrumentation
                trap_offset_delta = trap_map[original_offset] << 1
                new_data.append(trap_offset_delta)
                new_data.append(128)  # No line number
                offset += trap_offset_delta

            offset_delta = next(data_iter)
            line_delta = next(data_iter)

            # If the current offset delta is 0, it means we are only incrementing the amount of lines jumped by the
            # next non-zero offset. See <https://github.com/python/cpython/blob/3.10/Objects/lnotab_notes.txt>.
            while offset_delta == 0:
                new_data.append(offset_delta)
                new_data.append(line_delta)
                offset_delta = next(data_iter)
                line_delta = next(data_iter)

            original_offset += offset_delta
            offset += offset_delta
            if ext_arg_offset is not None and ext_arg_size is not None and offset > ext_arg_offset:
                new_offset_delta = offset_delta + (ext_arg_size << 1)
                new_data.append(new_offset_delta & 0xFF)
                new_data.append(line_delta)
                new_offset_delta >>= 8
                while new_offset_delta:
                    new_data.append(new_offset_delta & 0xFF)
                    new_data.append(0)
                    new_offset_delta >>= 8
                offset += ext_arg_size << 1

                ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))
            else:
                new_data.append(offset_delta)
                new_data.append(line_delta)
        except StopIteration:
            break

    return bytes(new_data)


LOAD_CONST = dis.opmap["LOAD_CONST"]
CALL = dis.opmap["CALL_FUNCTION"]
POP_TOP = dis.opmap["POP_TOP"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]


def trap_call(trap_index: int, arg_index: int) -> t.Tuple[Instruction, ...]:
    return (
        *instr_with_arg(LOAD_CONST, trap_index),
        *instr_with_arg(LOAD_CONST, arg_index),
        Instruction(NO_OFFSET, CALL, 1),
        Instruction(NO_OFFSET, POP_TOP, 0),
    )

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

    new_offsets = {} # [None] * len(old_code)
    old_targets = {} # [None] * len(old_code)
    line_starts = dict(dis.findlinestarts(code))

    instructions = []

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


    for old_offset in range(0, len(old_code), 2):
        op = old_code[old_offset]
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

            # instructions.append((None, 'INSTRUMENT', line))
            pass

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

        else:
            new_code.append(op)
            new_code.append(arg & 0xFF)
            arg >>= 8
            while arg:
                # insert extended args BEFORE op
                new_code.insert(new_offset, EXTENDED_ARG)
                new_code.insert(new_offset+1, arg & 0xFF)
                arg >>= 8

    update_line(len(new_code) - new_offset, previous_line - previous_previous_line)

    # Fixup the offsets.
    for old_offset, old_target in old_targets.items():
        new_offset = new_offsets[old_offset]
        new_target = new_offsets[old_target]
        op = new_code[new_offset + 6]
        if op in ABSOLUTE_JUMPS:
            arg = new_target // 2
        elif op in FORWARD_JUMPS:
            arg = (new_target - (new_offset + 8)) // 2
        elif op in BACKWARD_JUMPS:
            arg = ((new_offset + 8) - new_target) // 2
        else:
            raise NotImplementedError("oops")

        new_code[new_offset + 1] = (arg >> 24) & 0xFF
        new_code[new_offset + 3] = (arg >> 16) & 0xFF
        new_code[new_offset + 5] = (arg >>  8) & 0xFF
        new_code[new_offset + 7] = arg         & 0xFF

    seen_lines = CoverageLines() # TODO

    return code.replace(
        co_code=bytes(new_code),
        #co_consts=tuple(new_consts),
        co_linetable=bytes(new_linetable),
        co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
    ), seen_lines







def instrument_all_lines_OLD(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    # TODO[perf]: Check if we really need to << and >> everywhere
    trap_func, trap_arg = hook, path

    instructions: t.List[Instruction] = []

    new_consts = list(code.co_consts)
    trap_index = len(new_consts)
    new_consts.append(trap_func)

    seen_lines = CoverageLines()

    offset_map = {}

    # Collect all the original jumps
    jumps: t.Dict[int, Jump] = {}
    traps: t.Dict[int, int] = {}  # DEV: This uses the original offsets
    line_map = {}
    line_starts = dict(dis.findlinestarts(code))

    # The previous two arguments are kept in order to track the depth of the IMPORT_NAME
    # For example, from ...package import module
    current_arg: int = 0
    previous_arg: int = 0
    previous_previous_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    try:
        code_iter = iter(enumerate(code.co_code))
        ext = 0 #: list[int] = []
        while True:
            original_offset, opcode = next(code_iter)
            line = line_starts.get(original_offset)
            if line is not None:
                # Inject trap call at the beginning of the line. Keep track
                # of location and size of the trap call instructions. We
                # need this to adjust the location table.
                trap_instructions = (
                    *instr_with_arg(LOAD_CONST, trap_index),
                    *instr_with_arg(LOAD_CONST, len(new_consts)),
                    Instruction(NO_OFFSET, CALL, 1),
                    Instruction(NO_OFFSET, POP_TOP, 0),
                ) #trap_call(trap_index, len(new_consts))
                traps[original_offset] = len(trap_instructions)
                instructions.extend(trap_instructions)

                # Make sure that the current module is marked as depending on its own package by instrumenting the
                # first executable line
                package_dep = None
                if code.co_name == "<module>" and len(new_consts) == len(code.co_consts) + 1:
                    package_dep = (package, ("",))

                new_consts.append((line, trap_arg, package_dep))

                line_map[original_offset] = trap_instructions[0]

                seen_lines.add(line)

            _, arg = next(code_iter)

            offset = len(instructions) << 1

            # Propagate code
            instructions.append(Instruction(original_offset, opcode, arg))

            if opcode is EXTENDED_ARG:
                ext = (ext + arg) << 8
                continue
            else:
                previous_previous_arg = previous_arg
                previous_arg = current_arg
                current_arg = ext + arg # int.from_bytes([*ext, arg], "big", signed=False)
                ext = 0

            # Track imports names
            if opcode == IMPORT_NAME:
                import_depth = code.co_consts[previous_previous_arg]
                current_import_name = code.co_names[current_arg]
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
            if opcode == IMPORT_FROM:
                import_from_name = f"{current_import_name}.{code.co_names[current_arg]}"
                new_consts[-1] = (
                    new_consts[-1][0],
                    new_consts[-1][1],
                    (new_consts[-1][2][0], tuple(list(new_consts[-1][2][1]) + [import_from_name])),
                )

            # Collect branching instructions for processing
            if opcode in AJump.__opcodes__:
                jumps[offset] = AJump(original_offset, current_arg)
            elif opcode in RJump.__opcodes__:
                jumps[offset] = RJump(original_offset, current_arg, JumpDirection.from_opcode(opcode))

    except StopIteration:
        pass

    # Collect all the old jump start and end offsets
    jump_targets = {_ for j in jumps.values() for _ in (j.start, j.end)}

    # Adjust all the offsets and map the old offsets to the new ones for the
    # jumps
    for index, instr in enumerate(instructions):
        new_offset = index << 1
        if instr.offset in jump_targets:
            offset_map[instr.offset] = new_offset
        instr.offset = new_offset

    # Adjust all the jumps, neglecting any EXTENDED_ARGs for now
    branches: t.List[Branch] = []
    for jump in jumps.values():
        new_start = offset_map[jump.start]
        new_end = offset_map[jump.end]

        # If we are jumping at the beginning of a line, jump to the
        # beginning of the trap call instead
        target_instr = line_map.get(jump.end, instructions[new_end >> 1])
        branch: Branch = (
            RBranch(instructions[new_start >> 1], target_instr)
            if isinstance(jump, RJump)
            else ABranch(instructions[new_start >> 1], target_instr)
        )
        target_instr.targets.append(branch)

        branches.append(branch)

    # Process all the branching instructions to adjust the arguments. We
    # need to add EXTENDED_ARGs if the argument is too large.
    process_branches = True
    exts: t.List[t.Tuple[Instruction, int]] = []
    while process_branches:
        process_branches = False
        for branch in branches:
            jump_instr = branch.start
            new_arg = branch.arg
            jump_instr.arg = new_arg & 0xFF
            new_arg >>= 8
            c = 0
            index = jump_instr.offset >> 1

            # Update the argument of the branching instruction, adding
            # EXTENDED_ARGs if needed
            while new_arg:
                if index and instructions[index - 1].opcode is EXTENDED_ARG:
                    index -= 1
                    instructions[index].arg = new_arg & 0xFF
                else:
                    ext_instr = Instruction(index << 1, EXTENDED_ARG, new_arg & 0xFF)
                    instructions.insert(index, ext_instr)
                    c += 1
                    # If the jump instruction was a target of another jump,
                    # make the latest EXTENDED_ARG instruction the target
                    # of that jump.
                    if jump_instr.targets:
                        for target in jump_instr.targets:
                            if target.end is not jump_instr:
                                raise ValueError("Invalid target")
                            target.end = ext_instr
                        ext_instr.targets.extend(jump_instr.targets)
                        jump_instr.targets.clear()
                new_arg >>= 8

            # Check if we added any EXTENDED_ARGs because we would have to
            # reprocess the branches.
            # TODO[perf]: only reprocess the branches that are affected.
            # However, this branch is not expected to be taken often.
            if c:
                exts.append((ext_instr, c))
                # Update the instruction offset from the point of insertion
                # of the EXTENDED_ARGs
                for instr_index, instr in enumerate(instructions[index + 1 :], index + 1):
                    instr.offset = instr_index << 1

                process_branches = True

    # Create the new code object
    new_code = bytearray()
    for instr in instructions:
        new_code.append(instr.opcode)
        new_code.append(instr.arg)

    # Instrument nested code objects recursively
    for original_offset, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType):
            new_consts[original_offset], nested_lines = instrument_all_lines(nested_code, trap_func, trap_arg, package)
            seen_lines.update(nested_lines)

    return (
        code.replace(
            co_code=bytes(new_code),
            co_consts=tuple(new_consts),
            co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
            co_linetable=update_location_data(code, traps, [(instr.offset, s) for instr, s in exts]),
        ),
        seen_lines,
    )
