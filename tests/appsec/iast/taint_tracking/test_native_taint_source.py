from tests.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import Source


def test_all_params_and_tostring():
    s = Source(name="name", value="val", origin=OriginType.COOKIE)
    tostr = s.to_string()
    assert tostr == repr(s)
    assert tostr == s.__repr__()
    assert tostr == str(s)
    assert tostr.startswith("Source at ")
    assert tostr.endswith(" [name=name, value=val, origin=http.request.cookie.value]")
    assert s.name == "name"
    assert s.value == "val"
    assert s.origin == OriginType.COOKIE


def test_partial_params():
    s = Source()
    assert s.name == ""
    assert s.value == ""
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=, value=, origin=http.request.parameter]")

    s = Source("name")
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=name, value=, origin=http.request.parameter]")

    s = Source("name", "value")
    assert s.name == "name"
    assert s.value == "value"
    assert s.origin == OriginType.PARAMETER
    assert s.to_string().endswith(" [name=name, value=value, origin=http.request.parameter]")

    s = Source("name", origin=OriginType.BODY)
    assert s.name == "name"
    assert s.value == ""
    assert s.origin == OriginType.BODY
    assert s.to_string().endswith(" [name=name, value=, origin=http.request.body]")


def test_equals():
    s1 = Source("name", "value", OriginType.PARAMETER)
    also_s1 = Source("name", "value", OriginType.PARAMETER)
    s2 = Source("name", origin=OriginType.PARAMETER)
    s3 = Source(value="value")
    assert s1.__eq__(also_s1)
    assert s1 == also_s1

    assert s1 != s2
    assert s1 != s3
    assert s2 != s3
