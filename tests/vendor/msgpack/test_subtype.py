#!/usr/bin/env python

from collections import namedtuple

from msgpack import unpackb

from ddtrace.internal.native._msgpack import packb


class MyList(list[object]):
    pass


class MyDict(dict[object, object]):
    pass


class MyTuple(tuple[object, ...]):
    pass


MyNamedTuple = namedtuple("MyNamedTuple", "x y")


def test_types():
    assert unpackb(packb(MyDict())) == "Can not serialize [MyDict] object"
    assert unpackb(packb(MyList())) == "Can not serialize [MyList] object"
    assert unpackb(packb(MyNamedTuple(1, 2))) == "Can not serialize [MyNamedTuple] object"
