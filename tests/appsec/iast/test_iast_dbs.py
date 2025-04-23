import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


_ = _iast_patched_module("pymysql.connections")
_ = _iast_patched_module("pymysql.converters")
_ = _iast_patched_module("mysql.connector.conversion")
mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_dbs", should_patch_iast=True)


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
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)


def test_lazy_taint_list():
    obj_list = [1, "word", True]
    lazy_list = LazyTaintList(obj_list, origins=(OriginType.PARAMETER, OriginType.PARAMETER))

    value = mod.adapt_list(lazy_list)
    assert value == b"ARRAY[1,'word',true]"
    assert get_tainted_ranges(value)
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


def test_sanitize_mysql_connector_scape():
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.mysql_connector_scape(tainted)
    ranges = get_tainted_ranges(value)
    assert value == "a-\\'; DROP TABLE users; --"
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)
    assert is_pyobject_tainted(value)


def test_sanitize_pymysql_escape_string():
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.pymysql_escape_string(tainted)
    ranges = get_tainted_ranges(value)
    assert value == "a-\\'; DROP TABLE users; --"
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)
    assert is_pyobject_tainted(value)


def test_sanitize_pymysql_converters_escape():
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_sanitize_pymysql_converters",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.pymysql_converters_escape_string(tainted)
    ranges = get_tainted_ranges(value)
    assert value == "a-\\'; DROP TABLE users; --"
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)
    assert is_pyobject_tainted(value)
