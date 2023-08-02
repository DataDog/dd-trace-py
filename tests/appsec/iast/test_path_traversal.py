import os

import pytest

from ddtrace.appsec.iast._util import _is_python_version_supported as python_supported_by_iast
from tests.appsec.iast.aspects.conftest import _iast_patched_module


try:
    from ddtrace.appsec._constants import IAST
    from ddtrace.appsec.iast.constants import VULN_PATH_TRAVERSAL
    from ddtrace.internal import core
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


FIXTURES_PATH = "tests/appsec/iast/fixtures/path_traversal.py"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_path_traversal(iast_span_defaults):
    from ddtrace.appsec.iast._taint_tracking import setup

    mod = _iast_patched_module("tests.appsec.iast.fixtures.path_traversal")
    setup(bytes.join, bytearray.join)

    file_path = os.path.join(ROOT_DIR, "fixtures", "path_traversal_test_file.txt")
    mod.pt_open(file_path)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert list(span_report.vulnerabilities)[0].type == VULN_PATH_TRAVERSAL
    assert list(span_report.vulnerabilities)[0].location.path == FIXTURES_PATH
    assert list(span_report.vulnerabilities)[0].location.line == 18
    assert list(span_report.vulnerabilities)[0].evidence.value == file_path
