from abc import ABC
import dis
from enum import Enum
import sys
from types import CodeType
import typing as t

from ddtrace.internal.injection import HookType


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 8) and sys.version_info < (3, 9)  # nosec


class JumpDirection(int, Enum):
    FORWARD = 1
    BACKWARD = -1

    @classmethod
    def from_opcode(cls, opcode: int) -> "JumpDirection":
        return cls.BACKWARD if "BACKWARD" in dis.opname[opcode] else cls.FORWARD


class Jump(ABC):
    # NOTE: in Python 3.9, jump arguments are offsets, vs instruction numbers (ie offsets/2) in Python 3.10
    def __init__(self, start: int, argbytes: t.List[int]) -> None:
        self.start = start
        self.end: int
        self.arg = int.from_bytes(argbytes, "big", signed=False)
        self.argsize = len(argbytes)


class AJump(Jump):
    __opcodes__ = set(dis.hasjabs)

    def __init__(self, start: int, arg: t.List[int]) -> None:
        super().__init__(start, arg)
        self.end = self.arg


class RJump(Jump):
    __opcodes__ = set(dis.hasjrel)

    def __init__(self, start: int, arg: t.List[int], direction: JumpDirection) -> None:
        super().__init__(start, arg)
        self.direction = direction
        self.end = start + (self.arg) * self.direction + 2


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


def instr_with_arg(opcode: int, arg: int) -> t.List[Instruction]:
    instructions = [Instruction(-1, opcode, arg & 0xFF)]
    arg >>= 8
    while arg:
        instructions.insert(0, Instruction(-1, EXTENDED_ARG, arg & 0xFF))
        arg >>= 8
    return instructions


def update_location_data(
    code: CodeType, trap_map: t.Dict[int, int], ext_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    # Some code objects do not have co_lnotab data (eg: certain lambdas)
    if code.co_lnotab == b"":
        return code.co_lnotab

    # DEV: We expect the original offsets in the trap_map
    new_data = bytearray()
    data = code.co_lnotab

    ext_arg_offset_iter = iter(sorted(ext_arg_offsets))
    ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))

    current_orig_offset = 0  # Cumulative offset used to compare against trap offsets
    current_new_offset = current_orig_offset  # Cumulative offset used to compare against extended args offsets

    # In 3.8 , all instructions have to have line numbers, so the first instructions of the trap call must mark the
    # beginning of the line. The subsequent offsets need to be incremented by the size of the trap call instructions
    # plus any extended args.

    # Set the first trap size:
    current_new_offset = accumulated_new_offset = trap_map[0] << 1

    for i in range(0, len(data), 2):
        orig_offset_delta = data[i]
        line_delta = data[i + 1]

        # For each original offset, we compute how many offsets have been added in the new code, this includes:
        # - the size of the trap at the previous offset
        # - the amount of extended args added since the previous offset

        current_new_offset += orig_offset_delta
        current_orig_offset += orig_offset_delta
        accumulated_new_offset += orig_offset_delta

        # If the current offset is 255, just increment:
        if orig_offset_delta == 255:
            continue

        # If the current offset is 0, it means we are only incrementing the amount of lines jumped by the previous
        # non-zero offset
        if orig_offset_delta == 0:
            new_data.append(0)
            new_data.append(line_delta)
            continue

        while ext_arg_offset is not None and ext_arg_size is not None and current_new_offset > ext_arg_offset:
            accumulated_new_offset += ext_arg_size << 1
            current_new_offset += ext_arg_size << 1
            ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))

        # If the current line delta changes, flush accumulated data:
        if line_delta != 0:
            while accumulated_new_offset > 255:
                new_data.append(255)
                new_data.append(0)
                accumulated_new_offset -= 255

            new_data.append(accumulated_new_offset)
            new_data.append(line_delta)

            # Also add the current trap size to the accumulated offset
            accumulated_new_offset = trap_map[current_orig_offset] << 1
            current_new_offset += accumulated_new_offset

    return bytes(new_data)


LOAD_CONST = dis.opmap["LOAD_CONST"]
CALL = dis.opmap["CALL_FUNCTION"]
POP_TOP = dis.opmap["POP_TOP"]


def trap_call(trap_index: int, arg_index: int) -> t.Tuple[Instruction, ...]:
    return (
        *instr_with_arg(LOAD_CONST, trap_index),
        *instr_with_arg(LOAD_CONST, arg_index),
        Instruction(-1, CALL, 1),
        Instruction(-1, POP_TOP, 0),
    )


def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
    # TODO[perf]: Check if we really need to << and >> everywhere
    trap_func, trap_arg = hook, path

    instructions: t.List[Instruction] = []

    new_consts = list(code.co_consts)
    trap_index = len(new_consts)
    new_consts.append(trap_func)

    seen_lines = set()

    offset_map = {}

    # Collect all the original jumps
    jumps: t.Dict[int, Jump] = {}
    traps: t.Dict[int, int] = {}  # DEV: This uses the original offsets
    line_map = {}
    line_starts = dict(dis.findlinestarts(code))

    try:
        code_iter = iter(enumerate(code.co_code))
        ext: list[int] = []
        while True:
            original_offset, opcode = next(code_iter)

            if original_offset in line_starts:
                # Inject trap call at the beginning of the line. Keep track
                # of location and size of the trap call instructions. We
                # need this to adjust the location table.
                line = line_starts[original_offset]
                trap_instructions = trap_call(trap_index, len(new_consts))
                traps[original_offset] = len(trap_instructions)
                instructions.extend(trap_instructions)
                new_consts.append((line, trap_arg))

                line_map[original_offset] = trap_instructions[0]

                seen_lines.add(line)

            _, arg = next(code_iter)

            offset = len(instructions) << 1

            # Propagate code
            instructions.append(Instruction(original_offset, opcode, arg))

            # Collect branching instructions for processing
            if opcode in AJump.__opcodes__:
                jumps[offset] = AJump(original_offset, [*ext, arg])
            elif opcode in RJump.__opcodes__:
                jumps[offset] = RJump(original_offset, [*ext, arg], JumpDirection.from_opcode(opcode))

            if opcode is EXTENDED_ARG:
                ext.append(arg)
            else:
                ext.clear()
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
            new_arg = branch.arg << 1  # 3.9 uses offsets, not instruction numbers
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
                                raise (ValueError("Jump instruction is not the end of the branch"))
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
            new_consts[original_offset], nested_lines = instrument_all_lines(nested_code, trap_func, trap_arg)
            seen_lines.update(nested_lines)

    ext_arg_offsets = [(instr.offset, s) for instr, s in exts]

    return (
        code.replace(
            co_code=bytes(new_code),
            co_consts=tuple(new_consts),
            co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
            co_lnotab=update_location_data(code, traps, ext_arg_offsets),
        ),
        seen_lines,
    )
