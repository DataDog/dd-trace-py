import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


_ = _iast_patched_module("html")
_ = _iast_patched_module("markupsafe")
mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_sanitizers", should_patch_iast=True)


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


def test_werkzeug_secure_filename():
    file_path = "../../../etc/passwd; DROP TABLE users.txt"
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test",
        source_value=file_path,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.werkzeug_secure_filename(tainted)
    assert value == "a-etc_passwd_DROP_TABLE_users.txt"

    # TODO: the propagation doesn't work correctly in werkzeug.secure_filename because that function implements
    #  STRING.strip("._") which is not yet supported by IAST
    # ranges = get_tainted_ranges(value)
    # assert len(ranges) > 0
    # for _range in ranges:
    #     assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)
    # assert is_pyobject_tainted(value)


def test_werkzeug_secure_safe_join():
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
    # print(ranges)
    # assert len(ranges) > 0
    # for _range in ranges:
    #     assert _range.has_secure_mark(VulnerabilityType.XSS)
    # assert is_pyobject_tainted(value)
