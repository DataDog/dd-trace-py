#!/usr/bin/env python
# coding: utf-8

from msgpack import unpackb

from ddtrace.internal._encoding import packb


def test_unpack_bytearray():
    buf = bytearray(packb(["foo", "bar"]))
    obj = unpackb(buf)
    assert ["foo", "bar"] == obj


def test_unpack_memoryview():
    buf = bytearray(packb(["foo", "bar"]))
    view = memoryview(buf)
    obj = unpackb(view)
    assert ["foo", "bar"] == obj
