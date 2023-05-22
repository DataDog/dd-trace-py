import pytest
# try:
from ddtrace.appsec.iast._native import Source
# except (ImportError, AttributeError):
#     pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def test_all_params_and_tostring():
    s = Source(name="name", value="val", origin="origin")
    tostr = s.to_string()
    assert tostr == repr(s)
    assert tostr == s.__repr__()
    assert tostr == str(s)
    assert tostr.startswith("Source at ")
    assert tostr.endswith(" [name=name, value=val, origin=origin]")
    assert s.name == "name"
    assert s.value == "val"
    assert s.origin == "origin"


def test_partial_params():
    s = Source()
    assert s.name == ""
    assert s.value == ""
    assert s.origin == ""
    assert s.to_string().endswith(" [name=, value=, origin=]")

    s = Source("name")
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == ""
    assert s.to_string().endswith(" [name=name, value=, origin=]")

    s = Source("name", "value")
    assert s.name == "name"
    assert s.value == "value"
    assert s.origin == ""
    assert s.to_string().endswith(" [name=name, value=value, origin=]")

    s = Source("name", origin="origin")
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == "origin"
    assert s.to_string().endswith(" [name=name, value=, origin=origin]")


def test_equals():
    s1 = Source("name", "value", "origin")
    also_s1 = Source("name", "value", "origin")
    s2 = Source("name", origin="origin")
    s3 = Source(value="value")
    assert s1.__eq__(also_s1)
    assert s1 == also_s1

    assert s1 != s2
    assert s1 != s3
    assert s2 != s3
