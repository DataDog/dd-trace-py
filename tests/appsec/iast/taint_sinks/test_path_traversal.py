import os

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_env


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/path_traversal.py"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_path_traversal_module_functions():
    for module, functions in DEFAULT_PATH_TRAVERSAL_FUNCTIONS.items():
        for function in functions:
            if module not in ["posix", "_pickle"]:
                yield module, function


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_path_traversal_open(iast_span_defaults):
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
    assert len(span_report.vulnerabilities) == 1
    assert vulnerability.type == VULN_PATH_TRAVERSAL
    assert source.name == "path"
    assert source.origin == OriginType.PATH
    assert source.value == file_path
    assert vulnerability.evidence.valueParts == [{"source": 0, "value": file_path}]
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "file_path",
    (
        os.path.join(ROOT_DIR, "../../"),
        os.path.join(ROOT_DIR, "../../../"),
        os.path.join(ROOT_DIR, "../../../../"),
        os.path.join(ROOT_DIR, "../../../../../"),
        os.path.join(ROOT_DIR, "../../../../../../"),
    ),
)
def test_path_traversal_open_secure(file_path, iast_span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )
    mod.pt_open_secure(tainted_string)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report is None


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "module, function",
    _get_path_traversal_module_functions(),
)
def test_path_traversal(module, function, iast_span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "not_exists.txt")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )

    getattr(mod, "path_{}_{}".format(module, function))(tainted_string)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    line, hash_value = get_line_and_hash(
        "path_{}_{}".format(module, function), VULN_PATH_TRAVERSAL, filename=FIXTURES_PATH
    )
    vulnerability = list(span_report.vulnerabilities)[0]
    assert len(span_report.vulnerabilities) == 1
    assert vulnerability.type == VULN_PATH_TRAVERSAL
    assert vulnerability.location.path == FIXTURES_PATH
    assert vulnerability.location.line == line
    assert vulnerability.hash == hash_value
    assert vulnerability.evidence.valueParts == [{"source": 0, "value": file_path}]
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_path_traversal_deduplication(num_vuln_expected, iast_span_defaults):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")
    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "not_exists.txt")

    with override_env(dict(_DD_APPSEC_DEDUPLICATION_ENABLED="true")):
        tainted_string = taint_pyobject(
            file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
        )

        for _ in range(0, 5):
            mod.pt_open(tainted_string)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report

            assert len(span_report.vulnerabilities) == num_vuln_expected
