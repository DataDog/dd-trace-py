from dataclasses import dataclass
import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.injection import HookType
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10) and sys.version_info < (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
LOAD_CONST = dis.opmap["LOAD_CONST"]
CALL = dis.opmap["CALL_FUNCTION" if sys.version_info[:2] == (3, 10) else "CALL"]
POP_TOP = dis.opmap["POP_TOP"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]

JUMPS = set(dis.hasjabs + dis.hasjrel)
ABSOLUTE_JUMPS = set(dis.hasjabs)
BACKWARD_JUMPS = set(op for op in dis.hasjrel if "BACKWARD" in dis.opname[op])
FORWARD_JUMPS = set(op for op in dis.hasjrel if "BACKWARD" not in dis.opname[op])


@dataclass
class InjectionContext:
    original_code: CodeType
    hook: HookType
    offsets_cb: t.Callable[["InjectionContext"], t.List[int]]

    def transfer(self, code: CodeType) -> "InjectionContext":
        return InjectionContext(code, self.hook, self.offsets_cb)

    @property
    def injection_offsets(self) -> t.List[int]:
        return self.offsets_cb(self)


def inject_invocation(injection_context: InjectionContext, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    code = injection_context.original_code
    new_code, new_consts, new_linetable, new_exctable, seen_lines = _inject_invocation_nonrecursive(
        injection_context, path, package
    )

    # Instrument nested code objects recursively.
    for const_index, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType):
            new_consts[const_index], nested_lines = inject_invocation(
                injection_context.transfer(nested_code), path, package
            )
            seen_lines.update(nested_lines)

    code = code.replace(
        co_code=bytes(new_code),
        co_consts=tuple(new_consts),
        co_linetable=new_linetable,
        co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
        co_exceptiontable=new_exctable,
    )

    return (
        code,
        seen_lines,
    )


# This function returns a modified version of the bytecode for the given code object, such that the beginning of
# every new source code line is prepended a call to the hook function with an argument representing the line number,
# path and dependency information of the corresponding source code line.
#
# The hook function is added to the code constants. For each line, a new constant of the form (line, path,
# dependency_info) is also added to the code constants, and then a call to the hook with that constant is added to
# the bytecode. For example, let's say the hook function is added to the code constants at index 100, and the
# instructions corresponding to source code line 42 in file "foo.py" are:
#
#    1000 LOAD_CONST    1
#    1002 RETURN_VALUE  0
#
# Then an object of the form (42, "foo.py", None) would be added to the constants (let's say at index 101), and the
# resulting instrumented code would look like:
#
#    2000 LOAD_CONST 100    # index of the hook function
#    2002 LOAD_CONST 101    # index of the (42, "foo.py", None) object
#    2004 CALL 1            # call the hook function with the 1 provided argument
#    2006 POP_TOP 0         # discard the return value
#
#    2008 LOAD_CONST 1      # (original instructions)
#    2010 RETURN_VALUE 0    #
#
# Because the instruction offsets will change, jump targets have to be updated in the new bytecode. This is achieved
# in the following way:
#
#  - As we iterate through the code, we keep a dictionary `new_offsets` mapping the old offsets to the new ones.
#    If any instrumentation instructions have been prepended to the instruction, the new offset points to the
#    beginning of the instrumentation, not the instruction itself. In the example above, new_offsets[1000] would
#    be 2000, not 2008. This is so that any jump to the instruction will jump to the hook call instead.
#
#  - When we find a jump instruction, we update a dictionary `old_targets` mapping the (old) offset of the jump
#    instruction to the (old) offset of the target instruction. In the new bytecode, the jump will be emitted with a
#    placeholder jump target of zero, since we don't know the final locations of all instructions yet.
#
#  - After the new bytecode with placeholders has been generated for the whole code (and all new instruction offsets
#    are known), all the jump target placeholders are updated with the final offsets of the targets.
#
# One complicating factor here are EXTENDED_ARG instructions. Every bytecode instruction has exactly two bytes, one
# for the instruction opcode and one for the argument. If the argument (such as a jump target) exceeds 255, one or
# more EXTENDED_ARG instructions are prepended to the instruction to provide the extra bytes of the argument. For
# example, a jump to 131844 (0x020304) would look like this:
#
#         EXTENDED_ARG 2
#         EXTENDED_ARG 3
#         JUMP_ABSOLUTE 4
#
# When we see EXTENDED_ARG instructions, we have to accumulate the values of their arguments and combine them with
# the value of the next non-EXTENDED_ARG instruction. The `extended_arg` variable is used for that.
#
# To avoid having to deal with a variable number of EXTENDED_ARGs when patching jump target placeholders, all jump
# instructions in the generated bytecode are emitted with 3 EXTENDED_ARGs, like:
#
#         EXTENDED_ARG 0
#         EXTENDED_ARG 2
#         EXTENDED_ARG 3
#         JUMP_ABSOLUTE 4
#
# even if some of them are not strictly needed and will be left with a 0 argument. In this way, the code can be
# written as a two-step procedure: generating the bytecode with placeholders (without having to know how many
# EXTENDED_ARGs will be needed for the final offsets beforehand), then patching the placeholders (which are
# guaranteed not to change size during patching), which makes the code simpler and more efficient.
#
# Another mapping that is kept for jumps is `new_ends`, which maps old offsets to the new offset of the _end_ of the
# whole instrumented block of instructions (i.e., the offset of the instruction just after the jump). This is so
# that we can find the jump instruction itself in the new code (skipping the inserted instrumentation), and also
# because relative jumps are relative to the offset of the _next_ instruction.


def _inject_invocation_nonrecursive(
    injection_context: InjectionContext, path: str, package: str
) -> t.Tuple[bytearray, t.List[object], bytes, bytes, CoverageLines]:
    code = injection_context.original_code
    old_code = code.co_code
    new_code = bytearray()
    new_linetable = bytearray()

    is_python_3_11 = sys.version_info[:2] == (3, 11)

    previous_line = code.co_firstlineno
    previous_line_new_offset = 0
    previous_previous_line = 0

    arg = 0
    previous_arg = 0
    previous_previous_arg = 0
    extended_arg = 0
    original_extended_arg_count = 0
    extended_arg_offsets: t.List[t.Tuple[int, int]] = []

    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    new_offsets: t.Dict[int, int] = {}
    new_ends: t.Dict[int, int] = {}
    old_targets: t.Dict[int, int] = {}

    line_starts = dict(dis.findlinestarts(code))
    line_injection_offsets = injection_context.injection_offsets

    new_consts = list(code.co_consts)
    hook_index = len(new_consts)
    new_consts.append(injection_context.hook)

    seen_lines = CoverageLines()
    is_first_instrumented_module_line = code.co_name == "<module>"

    def append_instruction(opcode: int, extended_arg: int) -> bytearray:
        """
        Append an operation and its argument to the new bytecode.

        If the argument does not fit in a single byte, EXTENDED_ARG instructions are prepended as needed.
        """
        code_chunck = bytearray()

        if extended_arg > 255:
            extended_bytes = (extended_arg.bit_length() - 1) // 8
            shift = 8 * extended_bytes

            while shift:
                code_chunck.append(EXTENDED_ARG)
                code_chunck.append((extended_arg >> shift) & 0xFF)
                shift -= 8

        code_chunck.append(opcode)
        code_chunck.append(extended_arg & 0xFF)

        return code_chunck

    def update_linetable(offset_delta: int, line_delta: int) -> None:
        """
        Add a new line number update to the line table array.

        Conceptually, the line table registers which ranges of offsets are assigned a given line number. Since we need
        to know the start and end offsets of a given line, this function should be called _after_ a given line is
        finished, i.e., at the beginning of the next line, and after all lines have been processed.

        See <https://github.com/python/cpython/blob/3.10/Objects/lnotab_notes.txt> for details on the line table format.
        """
        # Offset delta is always positive. If the delta is more than 254, we keep adding increments of 254 with 0 line
        # change, until we reach the desired number.
        while offset_delta > 254:
            new_linetable.append(254)
            new_linetable.append(0)
            offset_delta -= 254

        new_linetable.append(offset_delta)

        # Line delta can be either positive or negative. If it's greater than 127 (or less than -127), we keep adding
        # increments of 127 or -127 with 0 offset change, until we reach the desired number.
        while line_delta > 127:
            new_linetable.append(127)  # line_delta
            new_linetable.append(0)  # offset_delta
            line_delta -= 127

        while line_delta < -127:
            new_linetable.append(0x81)  # line_delta
            new_linetable.append(0)  # offset_delta
            line_delta += 127

        # Finally, append anything left from the line delta.
        new_linetable.append(line_delta & 0xFF)

    # key: old offset, value: how many instructions have been injected at that spot
    offsets_map: t.Dict[int, int] = {}

    for old_offset in range(0, len(old_code), 2):
        opcode = old_code[old_offset]
        arg = old_code[old_offset + 1] | extended_arg

        new_offset = len(new_code)
        new_offsets[old_offset] = new_offset

        line = line_starts.get(old_offset)
        injected_opcodes_count = 0
        if line is not None:
            if old_offset > 0:
                # Beginning of new line: update line table entry for _previous_ line.
                update_linetable(new_offset - previous_line_new_offset, previous_line - previous_previous_line)
            previous_previous_line = previous_line
            previous_line = line
            previous_line_new_offset = new_offset

            seen_lines.add(line)
            if old_offset in line_injection_offsets:
                instructions = bytearray()

                if is_python_3_11:
                    instructions.append(dis.opmap["PUSH_NULL"])
                    instructions.append(0)

                instructions.extend(append_instruction(LOAD_CONST, hook_index))
                instructions.extend(append_instruction(LOAD_CONST, len(new_consts)))

                # DEV: Because these instructions have fixed arguments and don't need EXTENDED_ARGs, we append them
                #      directly to the bytecode here. This loop runs for every instruction in the code to be
                #      instrumented, so this has some impact on execution time.
                if is_python_3_11:
                    instructions.append(dis.opmap["PRECALL"])
                    instructions.append(1)
                    instructions.append(dis.opmap["CACHE"])
                    instructions.append(0)
                instructions.append(CALL)
                instructions.append(1)
                if is_python_3_11:
                    instructions.append(dis.opmap["CACHE"])
                    instructions.append(0)
                    instructions.append(dis.opmap["CACHE"])
                    instructions.append(0)
                    instructions.append(dis.opmap["CACHE"])
                    instructions.append(0)
                    instructions.append(dis.opmap["CACHE"])
                    instructions.append(0)
                instructions.append(POP_TOP)
                instructions.append(0)

                offsets_map[old_offset] = len(instructions) // 2
                injected_opcodes_count += len(instructions)
                new_code.extend(instructions)

                # Make sure that the current module is marked as depending on its own package by instrumenting the
                # first executable line.
                package_dep = None
                if is_first_instrumented_module_line:
                    package_dep = (package, ("",))
                    is_first_instrumented_module_line = False

                new_consts.append((line, path, package_dep))

        if opcode == EXTENDED_ARG:
            extended_arg = arg << 8
            original_extended_arg_count += 1
            continue

        extended_arg = 0

        if opcode in JUMPS:
            if opcode in ABSOLUTE_JUMPS:
                target = arg * 2
            elif opcode in FORWARD_JUMPS:
                target = old_offset + 2 + (arg * 2)
            elif opcode in BACKWARD_JUMPS:
                target = old_offset + 2 - (arg * 2)
            else:
                raise NotImplementedError(f"Unexpected instruction {opcode}")

            old_targets[old_offset] = target

            # Emit jump with placeholder 0 0 0 0 jump target.
            new_code.append(EXTENDED_ARG)
            new_code.append(0)
            new_code.append(EXTENDED_ARG)
            new_code.append(0)
            new_code.append(EXTENDED_ARG)
            new_code.append(0)
            new_code.append(opcode)
            new_code.append(0)

            new_ends[old_offset] = len(new_code)
            extended_arg_offsets.append((old_offset, 3 - original_extended_arg_count))

        else:
            new_code.extend(append_instruction(opcode, arg))

            # Track imports names
            if opcode == IMPORT_NAME:
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
            if opcode == IMPORT_FROM:
                import_from_name = f"{current_import_name}.{code.co_names[arg]}"
                new_consts[-1] = (
                    new_consts[-1][0],
                    new_consts[-1][1],
                    (new_consts[-1][2][0], tuple(list(new_consts[-1][2][1]) + [import_from_name])),
                )

        original_extended_arg_count = 0
        previous_previous_arg = previous_arg
        previous_arg = arg

    # Update line table for the last line we've seen.
    update_linetable(len(new_code) - previous_line_new_offset, previous_line - previous_previous_line)

    # Fixup the offsets.
    for old_offset, old_target in old_targets.items():
        new_offset = new_offsets[old_offset]
        new_target = new_offsets[old_target]
        new_end = new_ends[old_offset]
        opcode = old_code[old_offset]

        if opcode in ABSOLUTE_JUMPS:
            arg = new_target // 2
        elif opcode in FORWARD_JUMPS:
            arg = (new_target - new_end) // 2
        elif opcode in BACKWARD_JUMPS:
            arg = (new_end - new_target) // 2
        else:
            raise NotImplementedError(f"Unexpected instruction {opcode}")

        # The code to patch looks like <EXTENDED_ARG, 0, EXTENDED_ARG, 0, EXTENDED_ARG, 0, opcode, 0>.
        # Write each byte of the argument over the corresponding 0, starting from the end of the instruction.
        arg_offset = new_end - 1
        while arg:
            new_code[arg_offset] = arg & 0xFF
            arg >>= 8
            arg_offset -= 2

    return (
        new_code,
        new_consts,
        update_location_data(injection_context.original_code, offsets_map, extended_arg_offsets),
        generate_exception_table(injection_context.original_code, offsets_map, extended_arg_offsets),
        seen_lines,
    )


def update_location_data(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    See "Format of the location table" from Python's internal documentation for more information:
    https://github.com/python/cpython/blob/main/InternalDocs/code_objects.md#format-of-the-locations-table
    """
    # DEV: We expect the original offsets in the trap_map
    new_data = bytearray()

    data = code.co_linetable
    data_iter = iter(data)
    ext_arg_offset_iter = iter(sorted(extended_arg_offsets))
    ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))

    original_offset = 0
    offset = 0

    while True:
        try:
            chunk = bytearray()

            b = next(data_iter)

            chunk.append(b)

            offset_delta = ((b & 7) + 1) << 1  # multiply by 2 because the length in the line table is in code units.
            loc_code = (b >> 3) & 0xF

            # See https://github.com/python/cpython/blob/main/InternalDocs/code_objects.md#location-entries for
            # meaning of the `loc_code` value.
            if loc_code == 14:
                chunk.extend(consume_signed_varint(data_iter))
                for _ in range(3):
                    chunk.extend(consume_varint(data_iter))
            elif loc_code == 13:
                chunk.extend(consume_signed_varint(data_iter))
            elif 10 <= loc_code <= 12:
                for _ in range(2):
                    chunk.append(next(data_iter))
            elif 0 <= loc_code <= 9:
                chunk.append(next(data_iter))

            if original_offset in offsets_map:
                # No location info for the trap bytecode
                injected_code_units_size = offsets_map[original_offset]
                n, r = divmod(injected_code_units_size, 8)
                for _ in range(n):
                    new_data.append(0x80 | (0xF << 3) | 7)
                if r:
                    new_data.append(0x80 | (0xF << 3) | r - 1)
                offset += injected_code_units_size << 1

            # Extend the line table record if we added any EXTENDED_ARGs
            offset += offset_delta
            if ext_arg_offset is not None and original_offset > ext_arg_offset:
                # if ext_arg_offset is not None and offset > ext_arg_offset:
                room = 7 - offset_delta
                chunk[0] += min(room, t.cast(int, ext_arg_size))
                if room < t.cast(int, ext_arg_size):
                    chunk.append(0x80 | (0xF << 3) | t.cast(int, ext_arg_size) - room)
                offset += ext_arg_size << 1

                ext_arg_offset, ext_arg_size = next(ext_arg_offset_iter, (None, None))

            original_offset += offset_delta

            new_data.extend(chunk)
        except StopIteration:
            break

    return bytes(new_data)


def consume_varint(stream: t.Iterator[int]) -> bytes:
    a = bytearray()

    b = next(stream)
    a.append(b)

    value = b & 0x3F
    while b & 0x40:
        b = next(stream)
        a.append(b)

        value = (value << 6) | (b & 0x3F)

    return bytes(a)


consume_signed_varint = consume_varint


def generate_exception_table(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    For format see:
    https://github.com/python/cpython/blob/208b0fb645c0e14b0826c0014e74a0b70c58c9d6/InternalDocs/exception_handling.md#format-of-the-exception-table
    """
    parsed_exception_table = dis._parse_exception_table(code)

    def calculate_additional_offset(original_offset: int):
        from_offsets = sum([codeunits << 1 for off, codeunits in offsets_map.items() if off <= original_offset])
        from_extended_args = sum([codeunits << 1 for off, codeunits in extended_arg_offsets if off <= original_offset])
        return original_offset + from_offsets + from_extended_args

    table = bytearray()

    for entry in parsed_exception_table:
        print(entry)
        new_start = calculate_additional_offset(entry.start)
        new_end = calculate_additional_offset(entry.end)
        new_target = calculate_additional_offset(entry.target)

        size = new_end - new_start + 2
        table.extend(to_varint(new_start >> 1, True))
        table.extend(to_varint(size >> 1))
        table.extend(to_varint(new_target >> 1))
        table.extend(to_varint(((entry.depth << 1) | (1 if entry.lasti else 0))))

    return bytes(table)


def to_varint(value: int, set_begin_marker: bool = False) -> bytes:
    # Encode value as a varint on 7 bits (MSB comes first) and set
    # the begin marker if requested.
    temp = bytearray()
    if value < 0:
        raise ValueError("Invalid value for varint")
    while value:
        temp.insert(0, value & 63 | (64 if temp else 0))
        value >>= 6
    temp = temp or bytearray([0])
    if set_begin_marker:
        temp[0] |= 128
    return bytes(temp)
