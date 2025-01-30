import psycopg2.extensions as ext
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


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
