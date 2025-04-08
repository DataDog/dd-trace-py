import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_psycopg2")


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


def test_adapt_list():
    obj_list = [1, "word", True]

    value = mod.adapt_list(obj_list)
    assert value == b"ARRAY[1,'word',true]"
    assert not is_pyobject_tainted(value)


def test_lazy_taint_list():
    obj_list = [1, "word", True]
    lazy_list = LazyTaintList(obj_list, origins=(OriginType.PARAMETER, OriginType.PARAMETER))

    value = mod.adapt_list(lazy_list)
    assert value == b"ARRAY[1,'word',true]"
    assert is_pyobject_tainted(value)


def test_sanitize_quote_ident():
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.sanitize_quote_ident(tainted)
    assert value == 'a-"\'; DROP TABLE users; --"'
    assert not is_pyobject_tainted(value)
