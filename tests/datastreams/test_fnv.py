import random

from ddtrace.internal.utils.fnv import fnv1_64


def _reference_fnv1_64(data):
    # Pure-Python FNV-1 64-bit, kept as the reference for the native implementation.
    hval = 0xCBF29CE484222325
    for byte in data:
        hval = (hval * 0x100000001B3) % 2**64
        hval = hval ^ byte
    return hval


def test_fnv1_64_known_vectors():
    assert fnv1_64(b"") == 0xCBF29CE484222325
    assert fnv1_64(b"a") == 0xAF63BD4C8601B7BE
    assert fnv1_64(b"foobar") == 0x340D8765A4DDA9C2


def test_fnv1_64_matches_reference_on_random_inputs():
    rng = random.Random(830)
    for _ in range(2000):
        data = rng.randbytes(rng.randint(0, 256))
        assert fnv1_64(data) == _reference_fnv1_64(data)
