from dataclasses import dataclass
import dis
import sys
from types import CodeType
import typing as t


CallbackType = t.Callable[[t.Any], t.Any]

assert (3, 10) <= sys.version_info < (3, 12)  # nosec

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

is_python_3_10 = sys.version_info[:2] == (3, 10)
is_python_3_11 = sys.version_info[:2] == (3, 11)


@dataclass
class InjectionContext:
    original_code: CodeType
    hook: CallbackType
    offsets_callback: t.Callable[["InjectionContext"], t.List[int]]

    def transfer(self, code: CodeType) -> "InjectionContext":
        return InjectionContext(code, self.hook, self.offsets_callback)

    @property
    def injection_offsets(self) -> t.List[int]:
        return self.offsets_callback(self)


def inject_invocation(injection_context: InjectionContext, path: str, package: str) -> t.Tuple[CodeType, t.List[int]]:
    """
    Inject invocation of the hook function at the specified source code lines, or more specifically offsets,
    in the given code object. Injection is recursive, in case of nested code objects (e.g. inline functions).
    """
    code = injection_context.original_code
    new_code, new_consts, new_linetable, new_exctable, instrumented_lines = _inject_invocation_nonrecursive(
        injection_context, path, package
    )

    # Instrument nested code objects recursively.
    for const_index, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType):
            new_consts[const_index], nested_lines = inject_invocation(
                injection_context.transfer(nested_code), path, package
            )
            instrumented_lines.extend(nested_lines)

    if sys.version_info >= (3, 11):
        code = code.replace(
            co_code=new_code,
            co_consts=tuple(new_consts),
            co_linetable=new_linetable,
            co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
            co_exceptiontable=new_exctable,
        )
    else:
        code = code.replace(
            co_code=bytes(new_code),
            co_consts=tuple(new_consts),
            co_linetable=new_linetable,
            co_stacksize=code.co_stacksize + 4,  # TODO: Compute the value!
        )

    return (
        code,
        instrumented_lines,
    )


# This function is a modified and more generic version of the original [`_inject_invocation` function](
# https://github.com/DataDog/dd-trace-py/blob/3b076e624c12b76f304e6658e57c5c8b10a2082b/ddtrace/internal/coverage/instrumentation_py3_10.py#L116)
# from [vitor-de-araujo](https://github.com/DataDog/dd-trace-py/commits?author=vitor-de-araujo)
#
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
) -> t.Tuple[bytes, t.List[t.Any], bytes, bytes, t.List[int]]:
    """
    Inject invocation of the hook function at the specified source code lines, or more specifically offsets, in the
    given code object. Injection is non-recursive, i.e. it does not instrument nested code objects.
    """
    code = injection_context.original_code
    # if there is no place to inject code, we return the original code
    if len(injection_context.injection_offsets) == 0:
        exception_table = code.co_exceptiontable if is_python_3_11 else b""  # type: ignore

        return (
            code.co_code,
            list(code.co_consts),
            code.co_linetable,
            exception_table,
            [],
        )

    old_code = code.co_code
    new_code = bytearray()

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

    instrumented_lines: list[int] = []
    is_first_instrumented_module_line = code.co_name == "<module>"

    def append_instruction(opcode: int, extended_arg: int):
        """
        Append an operation and its argument to the new bytecode.

        If the argument does not fit in a single byte, EXTENDED_ARG instructions are prepended as needed.
        """
        if extended_arg > 255:
            extended_bytes = (extended_arg.bit_length() - 1) // 8
            shift = 8 * extended_bytes

            while shift:
                new_code.append(EXTENDED_ARG)
                new_code.append((extended_arg >> shift) & 0xFF)
                shift -= 8

        new_code.append(opcode)
        new_code.append(extended_arg & 0xFF)

    # key: old offset, value: how many instructions have been injected at that spot
    offsets_map: t.Dict[int, int] = {}

    injection_occurred = False
    for old_offset in range(0, len(old_code), 2):
        opcode = old_code[old_offset]
        arg = old_code[old_offset + 1] | extended_arg

        new_offset = len(new_code)
        new_offsets[old_offset] = new_offset

        line = line_starts.get(old_offset)
        if line is not None:
            injection_occurred = False
            if old_offset in line_injection_offsets:
                code_size_before_injection = len(new_code)

                if is_python_3_11:
                    new_code.append(dis.opmap["PUSH_NULL"])
                    new_code.append(0)

                append_instruction(LOAD_CONST, hook_index)
                append_instruction(LOAD_CONST, len(new_consts))

                # DEV: Because these instructions have fixed arguments and don't need EXTENDED_ARGs, we append them
                #      directly to the bytecode here. This loop runs for every instruction in the code to be
                #      instrumented, so this has some impact on execution time.
                if is_python_3_11:
                    new_code.append(dis.opmap["PRECALL"])
                    new_code.append(1)
                    new_code.append(dis.opmap["CACHE"])
                    new_code.append(0)
                new_code.append(CALL)
                new_code.append(1)
                if is_python_3_11:
                    new_code.append(dis.opmap["CACHE"])
                    new_code.append(0)
                    new_code.append(dis.opmap["CACHE"])
                    new_code.append(0)
                    new_code.append(dis.opmap["CACHE"])
                    new_code.append(0)
                    new_code.append(dis.opmap["CACHE"])
                    new_code.append(0)
                new_code.append(POP_TOP)
                new_code.append(0)

                offsets_map[old_offset] = (len(new_code) - code_size_before_injection) // 2
                injection_occurred = True
                instrumented_lines.append(line)

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
            append_instruction(opcode, arg)

            if injection_occurred:
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

    if is_python_3_11:
        exception_table = _generate_exception_table(code, offsets_map, extended_arg_offsets)
    else:
        exception_table = b""

    return (
        bytes(new_code),
        new_consts,
        _generate_adjusted_location_data(injection_context.original_code, offsets_map, extended_arg_offsets),
        exception_table,
        instrumented_lines,
    )


def _generate_adjusted_location_data(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    Generate python version's specific adjusted location data. This is needed to adjust the line number information
    since the format changed notably, for example, between Python 3.10 and 3.11.
    """

    if is_python_3_10:
        return _generate_adjusted_location_data_3_10(code, offsets_map, extended_arg_offsets)
    elif is_python_3_11:
        return _generate_adjusted_location_data_3_11(code, offsets_map, extended_arg_offsets)
    else:
        raise NotImplementedError(f"Unsupported Python version: {sys.version_info}")


def _generate_adjusted_location_data_3_10(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    The format of the linetable in python3.10 is detailed here:
    https://github.com/python/cpython/blob/3.10/Objects/lnotab_notes.txt

    TL;DR: an entry is composed of two bytes:
    - the first one represents the bytecode offset delta. It is unsigned
    - the second one represent the line delta. It is signed. -128 represents no line
    If the offsets of the opcode are from 0 to 6 on line 1 and from 6 to 50 on line two,
    we would encode:
    End-Start  Line-delta
      6         +1
      44        +1

    The injected bytecodes are always at the start of the line and will never add a line.
    """
    new_linetable = bytearray()

    # Iterate through existing variables
    offsets_iterator = iter(sorted(offsets_map.items(), key=lambda x: x[0]))
    extended_arg_iterator = iter(sorted(extended_arg_offsets, key=lambda x: x[0]))

    injection_offset, count_opcode_added = next(offsets_iterator, (None, None))
    extended_arg_offset, count_extended_arg_added = next(extended_arg_iterator, (None, None))

    old_linetable = code.co_linetable
    old_linetable_size = len(old_linetable)

    current_offset = 0
    for idx in range(0, old_linetable_size, 2):
        """
        We do not want to interfere with the line deltas. Therefore we will copy
        the line deltas of the old table and add delta offsets with a line delta
        of 0 if needed. Notes that this is not the most optimized linetable possible
        but it is working.
        """
        # Append the former linetable entry
        # Offset
        new_linetable.append(old_linetable[idx])
        # Line delta
        new_linetable.append(old_linetable[idx + 1])

        # It means the line delta is encoded on multiple entry
        if old_linetable[idx] == 0:
            continue

        # 0x80 means -128 which means no line. Inject on an opcode with no lines is not expected
        # but we still to handle the case
        line_delta = 0x80 if old_linetable[idx + 1] == 0x80 else 0

        # Check if we have injected some extended_arg at the current_offset
        while (
            extended_arg_offset is not None
            and count_extended_arg_added is not None
            and current_offset + old_linetable[idx] > extended_arg_offset
        ):
            offset_to_add = count_extended_arg_added << 1
            # if we added more than 254 offsets, we need multiple bytes to encode it
            n, r = divmod(offset_to_add, 254)
            for _ in range(n):
                new_linetable.append(254)
                new_linetable.append(line_delta)
            if r:
                new_linetable.append(r)
                new_linetable.append(line_delta)
            extended_arg_offset, count_extended_arg_added = next(extended_arg_iterator, (None, None))

        # Check if we have injected our callback at the current_offset
        while injection_offset is not None and count_opcode_added is not None and current_offset >= injection_offset:
            offset_to_add = count_opcode_added << 1
            # if we added more than 254 offsets, we need multiple bytes to encode it
            n, r = divmod(offset_to_add, 254)
            for _ in range(n):
                new_linetable.append(254)
                new_linetable.append(line_delta)
            if r:
                new_linetable.append(r)
                new_linetable.append(line_delta)
            injection_offset, count_opcode_added = next(offsets_iterator, (None, None))

        current_offset += old_linetable[idx]
    return bytes(new_linetable)


def _generate_adjusted_location_data_3_11(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    The format of the linetable in python3.11 is detailed here:
    https://github.com/python/cpython/blob/main/InternalDocs/code_objects.md#format-of-the-locations-table

    """
    new_linetable = bytearray()

    orignal_linetable = code.co_linetable
    linetable_iter = iter(orignal_linetable)
    ext_arg_offset_iter = iter(sorted(extended_arg_offsets))
    ext_arg_offset, count_extended_arg_added = next(ext_arg_offset_iter, (None, None))

    original_offset = 0
    while True:
        try:
            chunk = bytearray()

            b = next(linetable_iter)

            # We add the former entry
            chunk.append(b)

            op_code_offset = ((b & 7) + 1) << 1  # multiply by 2 because the length in the line table is in code units.
            loc_code = (b >> 3) & 0xF

            """Depending on the loc code, the former entry requires potentially more bytes.
            They will be added below

            See https://github.com/python/cpython/blob/main/InternalDocs/code_objects.md#location-entries for
            # meaning of the `loc_code` value.
            """
            if loc_code == 14:
                chunk.extend(_consume_signed_varint(linetable_iter))
                for _ in range(3):
                    chunk.extend(_consume_varint(linetable_iter))
            elif loc_code == 13:
                chunk.extend(_consume_signed_varint(linetable_iter))
            elif 10 <= loc_code <= 12:
                for _ in range(2):
                    chunk.append(next(linetable_iter))
            elif 0 <= loc_code <= 9:
                chunk.append(next(linetable_iter))

            """ When adding entry to the linetable for the offsets related to the callback
            or the extended args. We are using the short form (see the link above). The code is
            0 and we add 0 line. It is just to add the bytecode offsets. The maximum we can add by entry is 8
            (encoded 7)
            """
            # Extend the line table record if we injected our callback at this offset
            if original_offset in offsets_map:
                injected_code_units_size = offsets_map[original_offset]
                n, r = divmod(injected_code_units_size, 8)
                for _ in range(n):
                    chunk.append(0x80 | 7)
                    chunk.append(0x00)
                if r:
                    chunk.append(0x80 | r - 1)
                    chunk.append(0x00)

            # Extend the line table record if we added any EXTENDED_ARGs at this offset
            while (
                ext_arg_offset is not None
                and count_extended_arg_added is not None
                and original_offset >= ext_arg_offset
            ):
                n, r = divmod(count_extended_arg_added, 8)
                for _ in range(n):
                    chunk.append(0x80 | 7)
                    chunk.append(0x00)
                if r:
                    chunk.append(0x80 | r - 1)
                    chunk.append(0x00)
                ext_arg_offset, count_extended_arg_added = next(ext_arg_offset_iter, (None, None))

            original_offset += op_code_offset

            new_linetable.extend(chunk)
        except StopIteration:
            break

    return bytes(new_linetable)


def _consume_varint(stream: t.Iterator[int]) -> bytes:
    a = bytearray()

    b = next(stream)
    a.append(b)

    value = b & 0x3F
    while b & 0x40:
        b = next(stream)
        a.append(b)

        value = (value << 6) | (b & 0x3F)

    return bytes(a)


_consume_signed_varint = _consume_varint


def _generate_exception_table(
    code: CodeType, offsets_map: t.Dict[int, int], extended_arg_offsets: t.List[t.Tuple[int, int]]
) -> bytes:
    """
    For format see:
    https://github.com/python/cpython/blob/208b0fb645c0e14b0826c0014e74a0b70c58c9d6/InternalDocs/exception_handling.md#format-of-the-exception-table
    """
    parsed_exception_table = dis._parse_exception_table(code)  # type: ignore[attr-defined]

    def calculate_additional_offset(original_offset: int, is_start: bool = False) -> int:
        # We want to include the code we add in the exception table, so that the finally block is correctly executed
        # if an error arises in the injected code.
        if is_start:
            relevant_offsets = [
                (offset, codeunits) for offset, codeunits in offsets_map.items() if offset < original_offset
            ]
            relevant_extended_args = [
                (off, codeunits) for off, codeunits in extended_arg_offsets if off < original_offset
            ]
        else:
            relevant_offsets = [
                (offset, codeunits) for offset, codeunits in offsets_map.items() if offset <= original_offset
            ]
            relevant_extended_args = [
                (off, codeunits) for off, codeunits in extended_arg_offsets if off <= original_offset
            ]

        from_offsets = sum([codeunits << 1 for _, codeunits in relevant_offsets])
        from_extended_args = sum([codeunits << 1 for _, codeunits in relevant_extended_args])

        return original_offset + from_offsets + from_extended_args

    table = bytearray()

    for entry in parsed_exception_table:
        new_start = calculate_additional_offset(entry.start, is_start=True)
        new_end = calculate_additional_offset(entry.end - 2)
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
