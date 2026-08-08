import struct

from ddtrace.internal.utils.fnv import _get_byte


MAX_VAR_LEN_64 = 9


def encode_var_int_64(v: int) -> bytes:
    return encode_var_uint_64(v >> (64 - 1) ^ (v << 1))


def decode_var_int_64(b: bytes) -> tuple[int, bytes]:
    v, b = decode_var_uint_64(b)
    return (v >> 1) ^ -(v & 1), b


def encode_var_uint_64(v: int) -> bytes:
    b = b""
    for _ in range(0, MAX_VAR_LEN_64):
        if v < 0x80:
            break
        b += struct.pack("B", (v & 255) | 0x80)
        v >>= 7
    b += struct.pack("B", v & 255)
    return b


def var_uint_64_len(v: int) -> int:
    """Length in bytes of encode_var_uint_64(v), without building the bytes object.

    Mirrors encode_var_uint_64 exactly: it emits one continuation byte per 7-bit group while
    v >= 0x80, for at most MAX_VAR_LEN_64 iterations, then one final byte.
    """
    if v < 0x80:
        return 1
    continuation_bytes = (v.bit_length() - 1) // 7
    if continuation_bytes > MAX_VAR_LEN_64:
        continuation_bytes = MAX_VAR_LEN_64
    return continuation_bytes + 1


def var_int_64_len(v: int) -> int:
    """Length in bytes of encode_var_int_64(v), without building the bytes object."""
    return var_uint_64_len(v >> (64 - 1) ^ (v << 1))


def decode_var_uint_64(b: bytes) -> tuple[int, bytes]:
    x = 0
    s = 0
    for i in range(0, MAX_VAR_LEN_64):
        if len(b) <= i:
            raise EOFError()
        n = _get_byte(b[i])
        if n < 0x80 or i == MAX_VAR_LEN_64 - 1:
            return x | n << s, b[i + 1 :]
        x |= (n & 0x7F) << s
        s += 7
    raise EOFError
