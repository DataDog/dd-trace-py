import dis
import typing as t
from abc import ABC
from enum import Enum
from types import CodeType


class JumpDirection(int, Enum):
    FORWARD = 1
    BACKWARD = -1

    @classmethod
    def from_opcode(cls, opcode: int) -> "JumpDirection":
        return cls.BACKWARD if "BACKWARD" in dis.opname[opcode] else cls.FORWARD


class Jump(ABC):
    def __init__(self, start: int, arg: int) -> None:
        self.start = start
        self.end: t.Optional[int] = None
        self.arg = arg


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


class Branch:
    def __init__(self, start: Instruction, end: Instruction) -> None:
        self.start = start
        self.end = end

    @property
    def arg(self) -> int:
        return abs(self.end.offset - self.start.offset - 2) >> 1


EXTENDED_ARG = dis.EXTENDED_ARG
NO_OFFSET = -1


def inject_co_consts(consts: t.List, *args) -> t.Tuple[int, ...]:
    injection_indexes = []
    for a in args:
        injection_indexes.append(len(consts))
        consts.append(a)

    return tuple(injection_indexes)


def inject_co_varnames(vars: t.List, *args) -> t.Tuple[int, ...]:
    injection_indexes = []
    for a in args:
        injection_indexes.append(len(vars))
        vars.append(a)

    return tuple(injection_indexes)


def inject_co_names(names: t.List, *args: str) -> t.Tuple[int, ...]:
    injection_indexes = []
    for a in args:
        injection_indexes.append(len(names))
        names.append(a)

    return tuple(injection_indexes)


def instr_with_arg(opcode: int, arg: int) -> t.List[Instruction]:
    instructions = [Instruction(NO_OFFSET, opcode, arg & 0xFF)]
    arg >>= 8
    while arg:
        instructions.insert(0, Instruction(NO_OFFSET, EXTENDED_ARG, arg & 0xFF))
        arg >>= 8
    return instructions


def instructions_to_bytecode(instructions: t.List[Instruction]) -> bytes:
    new_code = bytearray()
    for instr in instructions:
        new_code.append(instr.opcode)
        if instr.opcode > dis.HAVE_ARGUMENT:
            new_code.append(instr.arg)
        else:
            new_code.append(0)

    return bytes(new_code)
