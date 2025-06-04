from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.iast.iast_utils import _iast_patched_module


def patch_modules():
    """We apply the IAST patch after `psycopg_patch` (see `conftest`) because if we load the module before
    `tracer.patch`, there’s a side effect:
    Correct order:
    ```python
    psycopg_patch()
    from psycopg2.extensions import quote_ident
    ```

    Incorrect order:
    ```python
    from psycopg2.extensions import quote_ident
    psycopg_patch()
    quote_ident("aaaa", connection)
    #                  ^^^^^^^^^^^^
    # TypeError: argument 2 must be a connection or a cursor
    ```

    In the wrong order, `quote_ident` doesn't recognize the patched connection object correctly,
    leading to a `TypeError`.
    """
    _ = _iast_patched_module("html")
    _ = _iast_patched_module("markupsafe")
    _ = _iast_patched_module("werkzeug.utils")
    _ = _iast_patched_module("pymysql.connections")
    _ = _iast_patched_module("pymysql.converters")
    _ = _iast_patched_module("mysql.connector.conversion")
    mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_sanitizers", should_patch_iast=True)
    return mod


def test_werkzeug_secure_filename():
    mod = patch_modules()
    file_path = "../../../etc/passwd; DROP TABLE users.txt"
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test",
        source_value=file_path,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.werkzeug_secure_filename(tainted)
    assert value == "a-etc_passwd_DROP_TABLE_users.txt"

    ranges = get_tainted_ranges(value)
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)
    assert is_pyobject_tainted(value)


def test_werkzeug_secure_safe_join():
    mod = patch_modules()
    filename = "Documents"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.werkzeug_secure_safe_join(tainted)
    assert value == "/var/www/uploads/Documents"

    # TODO: the propagation doesn't work correctly in werkzeug.safe_join because that function implements
    #  posixpath.normpath which is not yet supported by IAST
    # ranges = get_tainted_ranges(value)
    # assert len(ranges) > 0
    # for _range in ranges:
    #     assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)
    # assert is_pyobject_tainted(value)


def test_html_scape():
    mod = patch_modules()
    file_path = '<script>alert("XSS")</script>'
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test",
        source_value=file_path,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.html_scape(tainted)
    assert value == "&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;"

    ranges = get_tainted_ranges(value)
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.XSS)
    assert is_pyobject_tainted(value)


def test_markupsafe_scape():
    mod = patch_modules()
    file_path = '<script>alert("XSS")</script>'
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test",
        source_value=file_path,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.markupsafe_scape(tainted)
    assert value == "&lt;script&gt;alert(&#34;XSS&#34;)&lt;/script&gt;"

    # TODO: the propagation doesn't work correctly in markupsafe.scape because that function implements
    #  markupsafe._speedups._escape_inner which is not yet supported by IAST
    # ranges = get_tainted_ranges(value)
    # assert len(ranges) > 0
    # for _range in ranges:
    #     assert _range.has_secure_mark(VulnerabilityType.XSS)
    # assert is_pyobject_tainted(value)


def test_sanitize_mysql_connector_scape():
    mod = patch_modules()
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
    mod = patch_modules()
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
    mod = patch_modules()
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


def test_sanitize_quote_ident():
    mod = patch_modules()
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
