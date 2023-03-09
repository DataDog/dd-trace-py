#!/usr/bin/env python3

import pytest


try:
    from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_tracking import setup as taint_tracking_setup  # type: ignore[attr-defined]
    from ddtrace.appsec.iast._taint_utils import LazyTaintDict
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def setup():
    taint_tracking_setup(bytes.join, bytearray.join)


def test_tainted_getitem():
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    )

    # Strings are tainted, but integers are not
    assert is_pyobject_tainted(tainted_knights["gallahad"])
    assert not is_pyobject_tainted(tainted_knights["not string"])

    # Regular dict is not affected
    assert not is_pyobject_tainted(knights["gallahad"])

    # KeyError is raised if the key is not found
    with pytest.raises(KeyError):
        knights["arthur"]
    with pytest.raises(KeyError):
        tainted_knights["arthur"]


def test_tainted_get():
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    tainted_knights = LazyTaintDict(
        {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", "")), "not string": 1}
    )

    # Not-existing key returns None or default
    arthur = knights.get("arthur")
    assert arthur is None
    arthur = tainted_knights.get("arthur")
    assert arthur is None
    arthur = tainted_knights.get("arthur", "default")
    assert arthur == "default"
    assert not is_pyobject_tainted(arthur)

    # Integers are not tainted
    not_string = tainted_knights.get("not string")
    assert not is_pyobject_tainted(not_string)

    # String-like values are tainted
    tainted_robin = tainted_knights.get("robin")
    assert tainted_robin is not None
    assert is_pyobject_tainted(tainted_robin)

    # Regular dict is not affected
    robin = knights.get("robin")
    assert not is_pyobject_tainted(robin)


def test_tainted_items():
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))}
    tainted_knights = LazyTaintDict({"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))})

    # Values are tainted if string-like, keys aren't
    for k, v in tainted_knights.items():
        assert not is_pyobject_tainted(k)
        assert is_pyobject_tainted(v)

    # Regular dict is not affected
    for k, v in knights.items():
        assert not is_pyobject_tainted(k)
        assert not is_pyobject_tainted(v)


def test_tainted_values():
    knights = {"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))}
    tainted_knights = LazyTaintDict({"gallahad": "".join(("the pure", "")), "robin": "".join(("the brave", ""))})

    # Values are tainted if string-like
    for v in tainted_knights.values():
        assert is_pyobject_tainted(v)

    # Regular dict is not affected
    for v in knights.values():
        assert not is_pyobject_tainted(v)
