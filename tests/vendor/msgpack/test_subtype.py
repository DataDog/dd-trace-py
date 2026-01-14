#!/usr/bin/env python
# coding: utf-8

from collections import namedtuple

from msgpack import unpackb

from ddtrace.internal._encoding import packb


class MyList(list):
    pass


class MyDict(dict):
    pass


class MyTuple(tuple):
    pass


MyNamedTuple = namedtuple("MyNamedTuple", "x y")


def test_types():
    assert unpackb(packb(MyDict())) == "Can not serialize [MyDict] object"
    assert unpackb(packb(MyList())) == "Can not serialize [MyList] object"
    assert unpackb(packb(MyNamedTuple(1, 2))) == "Can not serialize [MyNamedTuple] object"
