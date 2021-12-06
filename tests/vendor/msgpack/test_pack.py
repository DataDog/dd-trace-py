#!/usr/bin/env python
# coding: utf-8
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from io import BytesIO
import struct

from msgpack import Unpacker
from msgpack import unpackb

from ddtrace.internal._encoding import Packer
from ddtrace.internal._encoding import packb


def check(data):
    re = unpackb(packb(data), raw=True)
    assert re == data


def testPack():
    test_data = [
        0,
        1,
        127,
        128,
        255,
        256,
        65535,
        65536,
        4294967295,
        4294967296,
        -1,
        -32,
        -33,
        -128,
        -129,
        -32768,
        -32769,
        -4294967296,
        -4294967297,
        1.0,
        b"",
        b"a",
        b"a" * 31,
        b"a" * 32,
        None,
        True,
        False,
        (1 << 23),
    ]
    for td in test_data:
        check(td)


def testPackUnicode():
    test_data = ["", "abcd", ["defgh"], "Русский текст"]
    for td in test_data:
        re = unpackb(packb(td), use_list=1, raw=False)
        assert re == td
        packer = Packer()
        data = packer.pack(td)
        re = Unpacker(BytesIO(data), raw=False, use_list=1).unpack()
        assert re == td


def testPackBytes():
    test_data = [
        b"",
        b"abcd",
        [b"defgh"],
    ]
    for td in test_data:
        check(td)


def testDecodeBinary():
    re = unpackb(packb(b"abc"), raw=True)
    assert re == b"abc"


def testPackFloat():
    assert packb(1.0) == b"\xcb" + struct.pack(str(">d"), 1.0)
