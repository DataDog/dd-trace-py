import psycopg2.extensions as ext

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.utils import override_env
from tests.utils import override_global_config


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import create_context
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


def setup_module():
    create_context()
    oce._enabled = True


def test_list():
    obj_list = [1, "word", True]

    value = ext.adapt(obj_list).getquoted()
    assert value == b"ARRAY[1,'word',true]"
    assert not is_pyobject_tainted(value)


def test_lazy_taint_list():
    with override_global_config(dict(_iast_enabled=True)):
        obj_list = [1, "word", True]
        lazy_list = LazyTaintList(obj_list, origins=(OriginType.PARAMETER, OriginType.PARAMETER))

        value = ext.adapt(lazy_list).getquoted()
        assert value == b"ARRAY[1,'word',true]"
        assert is_pyobject_tainted(value)
