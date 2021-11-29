#!/usr/bin/env python
# coding: utf-8

from collections import namedtuple

import pytest

from ddtrace.internal._encoding import packb


class MyList(list):
    pass


class MyDict(dict):
    pass


class MyTuple(tuple):
    pass


MyNamedTuple = namedtuple("MyNamedTuple", "x y")


def test_types():
    with pytest.raises(TypeError):
        assert packb(MyDict()) == packb(dict())
    with pytest.raises(TypeError):
        assert packb(MyList()) == packb(list())
    with pytest.raises(TypeError):
        assert packb(MyNamedTuple(1, 2)) == packb((1, 2))
