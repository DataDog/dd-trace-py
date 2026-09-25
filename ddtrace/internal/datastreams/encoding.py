MAX_VAR_LEN_64 = 9


def _zigzag(v: int) -> int:
    return v >> (64 - 1) ^ (v << 1)


def encode_var_int_64(v: int) -> bytes:
    return encode_var_uint_64(_zigzag(v))


def decode_var_int_64(b: bytes) -> tuple[int, bytes]:
    v, b = decode_var_uint_64(b)
    return (v >> 1) ^ -(v & 1), b


def encode_var_uint_64(v: int) -> bytes:
    b = bytearray()
    for _ in range(0, MAX_VAR_LEN_64):
        if v < 0x80:
            break
        b.append((v & 255) | 0x80)
        v >>= 7
    b.append(v & 255)
    return bytes(b)


def var_int_64_len(v: int) -> int:
    """Length of ``encode_var_int_64(v)``, computed without encoding."""
    u = _zigzag(v)
    n = 1
    while u >= 0x80 and n <= MAX_VAR_LEN_64:
        u >>= 7
        n += 1
    return n


def decode_var_int_64_at(b: bytes, pos: int) -> tuple[int, int]:
    """Like ``decode_var_int_64`` but reads at ``pos`` and returns the next position, avoiding slices."""
    x = 0
    s = 0
    end = len(b)
    for i in range(pos, pos + MAX_VAR_LEN_64):
        if i >= end:
            raise EOFError()
        n = b[i]
        if n < 0x80 or i == pos + MAX_VAR_LEN_64 - 1:
            v = x | n << s
            return (v >> 1) ^ -(v & 1), i + 1
        x |= (n & 0x7F) << s
        s += 7
    raise EOFError


def decode_var_uint_64(b: bytes) -> tuple[int, bytes]:
    x = 0
    s = 0
    for i in range(0, MAX_VAR_LEN_64):
        if len(b) <= i:
            raise EOFError()
        n = b[i]
        if n < 0x80 or i == MAX_VAR_LEN_64 - 1:
            return x | n << s, b[i + 1 :]
        x |= (n & 0x7F) << s
        s += 7
    raise EOFError
