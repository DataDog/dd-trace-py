import logging

import pytest

from ddtrace.appsec._iast._taint_tracking import _aspect_ospathjoin
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject


def test_ospathjoin_aspect():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospathjoin",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    print()
    print("JJJ ranges tainted_foo: %s" % get_tainted_ranges(tainted_foo))

    res = _aspect_ospathjoin("/root", tainted_foo, "nottainted", tainted_bar, "baz")
    print("JJJ res: %s" % res)
    print("JJJ ranges result: %s" % get_tainted_ranges(res))
    # res = _aspect_ospathjoin("/foo", "bar", "baz")
    # pass
