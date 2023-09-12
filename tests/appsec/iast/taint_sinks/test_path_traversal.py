import os

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/path_traversal.py"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_path_traversal(iast_span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "path_traversal_test_file.txt")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )
    mod.pt_open(tainted_string)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    vulnerability = list(span_report.vulnerabilities)[0]
    source = span_report.sources[0]
    assert vulnerability.type == VULN_PATH_TRAVERSAL
    assert source.name == "path"
    assert source.origin == OriginType.PATH
    assert source.value == file_path
    assert vulnerability.evidence.valueParts == [{"source": 0, "value": file_path}]
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None
