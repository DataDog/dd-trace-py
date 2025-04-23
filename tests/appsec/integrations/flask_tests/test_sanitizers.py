import pytest

from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_file_paths")


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        patch_iast()
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


def test_werkzeug_secure_filename():
    file_path = "../../../etc/passwd; DROP TABLE users.txt"
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test_sanitize_pymysql_converters",
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
        source_name="test_secure_filename_sanitizer",
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
