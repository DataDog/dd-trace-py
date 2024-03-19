from abc import ABC
from collections import deque
from dataclasses import dataclass
import dis
from enum import Enum
from types import CodeType
import typing as t

from bytecode import Bytecode

from ddtrace.internal.injection import HookType


def coalesce(lines):
    last_line = None
    new_lines = []
    for s, e, l in lines:
        if l is None:
            continue
        if l != last_line:
            new_lines.append((s, e, l))
            last_line = l
        else:
            new_lines[-1] = (new_lines[-1][0], e, l)
    return new_lines


def lines(code, offset):
    for i, (s, e, l) in enumerate(coalesce(code.co_lines())):
        if s <= offset < e:
            return i, l
    return None, None


def varint(bs):
    a = bytearray()

    b = next(bs)
    a.append(b)

    value = b & 0x3F
    while b & 0x40:
        b = next(bs)
        a.append(b)

        value = (value << 6) | (b & 0x3F)

    return a, value


def svarint(bs):
    a, value = varint(bs)
    return a, (-value if value & 1 else value) >> 1


def update_location_data(code: CodeType, extended_args_offsets) -> bytes:
    data = code.co_linetable
    new_data = bytearray()
    idata = iter(data)
    line = code.co_firstlineno
    ieaoff = iter(sorted(extended_args_offsets))
    neaoff, neasize = next(ieaoff, (None, None))

    offset = 0
    while True:
        try:
            chunk = bytearray()

            b = next(idata)

            chunk.append(b)

            offset_delta = ((b & 7) + 1) << 1
            loc_code = (b >> 3) & 0xF
            line_delta = 0

            if loc_code == 14:
                a, line_delta = svarint(idata)
                chunk.extend(a)
                for _ in range(3):
                    a, _ = varint(idata)
                    chunk.extend(a)
            elif loc_code == 13:
                a, line_delta = svarint(idata)
                chunk.extend(a)
            elif 10 <= loc_code <= 12:
                line_delta = loc_code - 10
                for _ in range(2):
                    chunk.append(next(idata))
            elif 0 <= loc_code <= 9:
                chunk.append(next(idata))

            if line_delta != 0 and code.co_code[offset] != dis.opmap["RESUME"]:
                # No location info for the trap bytecode
                n, r = divmod(TRAP_LENGTH, 8)
                for _ in range(n):
                    new_data.append(0x80 | (0xF << 3) | 7)
                if r:
                    new_data.append(0x80 | (0xF << 3) | r - 1)

            line += line_delta
            offset += offset_delta
            if neaoff is not None and offset > neaoff:
                room = 7 - offset_delta
                chunk[0] += min(room, t.cast(int, neasize))
                if room < t.cast(int, neasize):
                    chunk.append(0x80 | (0xF << 3) | t.cast(int, neasize) - room)
                neaoff, neasize = next(ieaoff, (None, None))

            new_data.extend(chunk)
        except StopIteration:
            break

    # assert bytes(new_data) == data

    return bytes(new_data)


def parse_varint(iterator):
    b = next(iterator)
    val = b & 63
    while b & 64:
        val <<= 6
        b = next(iterator)
        val |= b & 63
    return val


@dataclass
class ExceptionTableEntry:
    start: int
    end: int
    target: int
    depth: int
    lasti: bool


def parse_exception_table(code: CodeType):
    iterator = iter(code.co_exceptiontable)
    try:
        while True:
            start = parse_varint(iterator) * 2
            length = parse_varint(iterator) * 2
            end = start + length - 2  # Present as inclusive, not exclusive
            target = parse_varint(iterator) * 2
            dl = parse_varint(iterator)
            depth = dl >> 1
            lasti = bool(dl & 1)
            yield ExceptionTableEntry(start, end, target, depth, lasti)
    except StopIteration:
        return


def to_varint(value: int, set_begin_marker: bool = False) -> bytearray:
    # Encode value as a varint on 7 bits (MSB should come first) and set
    # the begin marker if requested.
    temp = bytearray()
    assert value >= 0
    while value:
        temp.insert(0, value & 63 | (64 if temp else 0))
        value >>= 6
    temp = temp or bytearray([0])
    if set_begin_marker:
        temp[0] |= 128
    return temp


def compile_exception_table(etab):
    table = bytearray()
    for entry in etab:
        size = entry.end - entry.start + 1
        depth = (entry.depth << 1) + entry.lasti
        table.extend(to_varint(entry.start >> 1, True))
        table.extend(to_varint(size >> 1))
        table.extend(to_varint(entry.target >> 1))
        table.extend(to_varint(depth))
    return table


# TODO: These depend on the Python version
def trap_call(findex, argindex):
    return bytes(
        [
            dis.opmap["PUSH_NULL"],
            0,
            # TODO: Handle large indices
            dis.opmap["LOAD_CONST"],
            findex,
            dis.opmap["LOAD_CONST"],
            argindex,
            dis.opmap["PRECALL"],
            1,
            dis.opmap["CACHE"],
            0,
            dis.opmap["CALL"],
            1,
            dis.opmap["CACHE"],
            0,
            dis.opmap["CACHE"],
            0,
            dis.opmap["CACHE"],
            0,
            dis.opmap["CACHE"],
            0,
            dis.opmap["POP_TOP"],
            0,
        ]
    )


TRAP_LENGTH = 11


class JumpDirection(int, Enum):
    FORWARD = 1
    BACKWARD = -1

    @classmethod
    def from_opcode(cls, opcode: int) -> "JumpDirection":
        return cls.BACKWARD if "BACKWARD" in dis.opname[opcode] else cls.FORWARD


class Jump(ABC):
    def __init__(self, start: int, argbytes: list[int]):
        self.start = start
        self.end: t.Optional[int] = None
        self.arg = int.from_bytes(argbytes, "big", signed=False)
        self.argsize = len(argbytes)

    def __eq__(self, __value: object) -> bool:
        return (
            isinstance(__value, self.__class__)
            and self.start == __value.start
            and self.end == __value.end
            and self.arg == __value.arg
        )

    def __gt__(self, offset):
        return min(self.start, self.end) >= offset

    def __repr__(self):
        return f"{self.__class__.__name__}(start={self.start}, end={self.end}, arg={self.arg})"


class AJump(Jump):
    __opcodes__ = set(dis.hasjabs)

    def __init__(self, start: int, arg: list[int]):
        super().__init__(start, arg)
        self.end = self.arg << 1  # TODO: Depends on the Python version


class RJump(Jump):
    __opcodes__ = set(dis.hasjrel)

    def __init__(self, start: int, arg: list[int], direction: JumpDirection):
        super().__init__(start, arg)
        self.direction = direction
        # TODO: Depends on the Python version
        self.end = start + (self.arg << 1) * self.direction + 2

    def __contains__(self, offset: int) -> bool:
        end = t.cast(int, self.end)
        # Prefixing instructions at either the start or end of relative
        # forward jumps does not modify the targets.
        return (self.start < offset < end) if self.start < end else (end <= offset <= self.start)


class BytecodeTransformer:
    def __init__(self, code):
        self.code = code
        self.new_code = bytearray()

        self.extended_args = deque()

    def adjust_arg(self, jump: Jump) -> int:
        offset = jump.start
        arg = jump.arg

        new_code = self.new_code

        # The only arguments we need to adjust are the branching instructions
        assert new_code[offset] in dis.hasjrel, dis.opname[new_code[offset]]

        # Update the opcode argument
        new_code[offset + 1] = arg & 0xFF

        # Update any EXTENDED_ARGs
        extra = []
        arg >>= 8
        while offset >= 2 and new_code[offset - 2] == dis.EXTENDED_ARG:
            offset -= 2
            new_code[offset + 1] = arg & 0xFF
            arg >>= 8
        while arg > 0:
            extra.append(arg & 0xFF)
            arg >>= 8
        if extra:
            self.extended_args.append((offset, jump.original.start, extra))

        return len(extra)

    def transform(self, trap, trap_arg=None) -> t.Tuple[CodeType, t.Set[int]]:
        code = self.code
        new_code = self.new_code

        new_consts = list(code.co_consts)
        trap_index = len(new_consts)
        new_consts.append(trap)

        last_line = None
        seen_lines = set()

        etab = list(parse_exception_table(code))
        etab_offsets = {_ for e in etab for _ in (e.start, e.end, e.target)}
        offset_map = {}

        # Collect all the original jumps
        jumps: t.Dict[int, Jump] = {}
        try:
            b = iter(enumerate(code.co_code))
            ext: list[bytes] = []
            while True:
                i, o = next(b)
                _, line = lines(code, i)
                seen_lines.add(line)

                if i in etab_offsets:
                    offset_map[i] = len(new_code)

                # TODO: There probably are other prefixes that should be skipped
                if line is not None and line != last_line and dis.opname[o] != "RESUME":
                    # Inject trap call at the beginning of the line
                    new_code.extend(trap_call(trap_index, len(new_consts)))
                    new_consts.append((line, trap_arg))

                    last_line = line

                _, arg = next(b)

                offset = len(new_code)

                # Propagate code
                new_code.append(o)
                new_code.append(arg)

                # Collect branching instructions for processing
                if o in AJump.__opcodes__:
                    jumps[offset] = AJump(i, [*ext, arg])
                elif o in RJump.__opcodes__:
                    jumps[offset] = RJump(i, [*ext, arg], JumpDirection.from_opcode(o))

                if o == dis.EXTENDED_ARG:
                    ext.append(arg)
                else:
                    ext.clear()
        except StopIteration:
            pass

        # Adjust all the jumps, neglecting any EXTENDED_ARGs for now
        new_jumps = []
        for offset, jump in jumps.items():
            is_relative = isinstance(jump, RJump)
            if is_relative:
                line_delta = lines(code, jump.end)[0] - lines(code, jump.start)[0] - 1
                new_arg = jump.arg + TRAP_LENGTH * line_delta * jump.direction
                new_jump = RJump(offset, new_arg.to_bytes(4, "big"), jump.direction)
                new_jump.original = jump
                self.adjust_arg(new_jump)

                assert new_code[offset] in dis.hasjrel, dis.opname[new_code[offset]]

                new_jumps.append(new_jump)

        # Do a similar thing with the exception table
        for e in etab:
            e.start = offset_map[e.start]
            e.end = offset_map[e.end]
            e.target = offset_map[e.target]

        new_etab = compile_exception_table(etab)

        # All the jumps have been processed, but we might have to add some EXTENDED_ARGs
        # and reprocess the affected jumps.
        extended_args = self.extended_args
        ext_args_offsets = {}
        while extended_args:
            offset, orig_offset, extra = extended_args.popleft()
            ext_args_offsets[orig_offset] = len(extra)

            # Add the EXTENDED_ARG(s) opcodes
            for b in extra:
                new_code[offset:offset] = (dis.EXTENDED_ARG, b)

            disp = len(extra) << 1

            # Update the offset map
            for k, v in offset_map.items():
                if v > offset:
                    offset_map[k] += disp

            # Update the start location of all the affected jumps
            for jump in new_jumps:
                if jump.start >= offset:
                    jump.start += disp
                    if isinstance(jump, RJump) and jump.end <= offset:
                        jump.arg += len(extra)
                        assert jump == RJump(jump.start, jump.arg.to_bytes(4, "big"), jump.direction), (
                            jump,
                            RJump(jump.start, jump.arg.to_bytes(4, "big"), jump.direction),
                        )

                        assert new_code[jump.start] in dis.hasjrel, dis.opname[new_code[jump.start]]

                elif jump.end > offset:
                    jump.end += disp
                    if isinstance(jump, RJump):
                        jump.arg += len(extra)
                        assert jump == RJump(jump.start, jump.arg.to_bytes(4, "big"), jump.direction), (
                            jump,
                            RJump(jump.start, jump.arg.to_bytes(4, "big"), jump.direction),
                        )

            # Check if we need to update any jumps
            for jump in new_jumps:
                # Relative jumps
                if isinstance(jump, RJump) and offset in jump:
                    self.adjust_arg(jump)

        # Instrumented nested code objects
        for i, nested_code in enumerate(code.co_consts):
            if isinstance(nested_code, CodeType):
                new_consts[i], nested_lines = BytecodeTransformer(nested_code).transform(trap, trap_arg)
                seen_lines.update(nested_lines)

        return CodeType(
            code.co_argcount,
            code.co_posonlyargcount,
            code.co_kwonlyargcount,
            code.co_nlocals,
            code.co_stacksize + 2,
            code.co_flags,
            bytes(new_code),
            tuple(new_consts),
            code.co_names,
            code.co_varnames,
            code.co_filename,
            code.co_name,
            code.co_qualname,
            code.co_firstlineno,
            update_location_data(code, ext_args_offsets.items()),
            bytes(new_etab),
            code.co_cellvars,
            code.co_freevars,
        ), seen_lines


def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
    return BytecodeTransformer(code).transform(hook, path)
