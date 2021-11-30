#!/usr/bin/env python
# coding: utf-8
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from msgpack import PackOverflowError
from msgpack import PackValueError
from msgpack import Packer
from msgpack import UnpackValueError
from msgpack import Unpacker
from msgpack import unpackb
import pytest

from ddtrace.internal._encoding import packb


def test_integer():
    x = -(2 ** 63)
    assert unpackb(packb(x)) == x
    with pytest.raises(PackOverflowError):
        packb(x - 1)

    x = 2 ** 64 - 1
    assert unpackb(packb(x)) == x
    with pytest.raises(PackOverflowError):
        packb(x + 1)


def test_array_header():
    packer = Packer()
    packer.pack_array_header(2 ** 32 - 1)
    with pytest.raises(PackValueError):
        packer.pack_array_header(2 ** 32)


def test_map_header():
    packer = Packer()
    packer.pack_map_header(2 ** 32 - 1)
    with pytest.raises(PackValueError):
        packer.pack_array_header(2 ** 32)


def test_max_str_len():
    d = "x" * 3
    packed = packb(d)

    unpacker = Unpacker(max_str_len=3, raw=False)
    unpacker.feed(packed)
    assert unpacker.unpack() == d

    unpacker = Unpacker(max_str_len=2, raw=False)
    with pytest.raises(UnpackValueError):
        unpacker.feed(packed)
        unpacker.unpack()


def test_max_array_len():
    d = [1, 2, 3]
    packed = packb(d)

    unpacker = Unpacker(max_array_len=3)
    unpacker.feed(packed)
    assert unpacker.unpack() == d

    unpacker = Unpacker(max_array_len=2)
    with pytest.raises(UnpackValueError):
        unpacker.feed(packed)
        unpacker.unpack()


def test_max_map_len():
    d = {1: 2, 3: 4, 5: 6}
    packed = packb(d)

    unpacker = Unpacker(max_map_len=3, strict_map_key=False)
    unpacker.feed(packed)
    assert unpacker.unpack() == d

    unpacker = Unpacker(max_map_len=2, strict_map_key=False)
    with pytest.raises(UnpackValueError):
        unpacker.feed(packed)
        unpacker.unpack()


# auto max len


def test_auto_max_array_len():
    packed = b"\xde\x00\x06zz"
    with pytest.raises(UnpackValueError):
        unpackb(packed, raw=False)

    unpacker = Unpacker(max_buffer_size=5, raw=False)
    unpacker.feed(packed)
    with pytest.raises(UnpackValueError):
        unpacker.unpack()


def test_auto_max_map_len():
    # len(packed) == 6 -> max_map_len == 3
    packed = b"\xde\x00\x04zzz"
    with pytest.raises(UnpackValueError):
        unpackb(packed, raw=False)

    unpacker = Unpacker(max_buffer_size=6, raw=False)
    unpacker.feed(packed)
    with pytest.raises(UnpackValueError):
        unpacker.unpack()
