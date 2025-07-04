import os
from unittest import mock

import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.appsec._iast.taint_sinks.path_traversal import check_and_report_path_traversal
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks._taint_sinks_utils import NON_TEXT_TYPES_TEST_DATA
from tests.appsec.iast.taint_sinks._taint_sinks_utils import ROOT_DIR


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/path_traversal.py"


@pytest.fixture
def ensure_test_file():
    """Fixture to ensure the test file exists for path traversal tests."""
    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "not_exists.txt")
    file_existed = os.path.exists(file_path)

    # Create the file if it doesn't exist
    if not file_existed:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write("test content for path traversal")

    yield file_path

    # Clean up: remove the file if we created it
    if not file_existed and os.path.exists(file_path):
        os.remove(file_path)


def _get_path_traversal_module_functions():
    for module, functions in DEFAULT_PATH_TRAVERSAL_FUNCTIONS.items():
        for function in functions:
            if module not in ["posix", "_pickle"]:
                yield module, function


def test_path_traversal_open(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "path_traversal_test_file.txt")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )
    mod.pt_open(tainted_string)

    data = _get_iast_data()

    assert len(data["vulnerabilities"]) == 1
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_PATH_TRAVERSAL
    assert source["name"] == "path"
    assert source["origin"] == OriginType.PATH
    assert source["value"] == file_path
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": file_path}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


@mock.patch("tests.appsec.iast.fixtures.taint_sinks.path_traversal.open")
def test_path_traversal_open_and_mock(mock_open, iast_context_defaults):
    """Confirm we can mock the open function and IAST path traversal vulnerability is not reported"""
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "path_traversal_test_file.txt")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )
    mod.pt_open(tainted_string)

    mock_open.assert_called_once_with(file_path)

    span_report = get_iast_reporter()
    assert span_report is None


def test_path_traversal_open_and_mock_after_patch_module(iast_context_defaults):
    """Confirm we can mock the open function and IAST path traversal vulnerability is not reported"""
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")
    with mock.patch("tests.appsec.iast.fixtures.taint_sinks.path_traversal.open") as mock_open:
        file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "path_traversal_test_file.txt")

        tainted_string = taint_pyobject(
            file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
        )
        mod.pt_open(tainted_string)

        mock_open.assert_called_once_with(file_path)

        span_report = get_iast_reporter()
        assert span_report is None


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
def test_path_traversal_open_secure(file_path, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )
    mod.pt_open_secure(tainted_string)
    span_report = get_iast_reporter()
    assert span_report is None


@pytest.mark.parametrize(
    "module, function",
    _get_path_traversal_module_functions(),
)
def test_path_traversal(module, function, iast_context_defaults, ensure_test_file):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")

    file_path = ensure_test_file

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )

    path = "path_{}_{}".format(module, function)
    getattr(mod, "path_{}_{}".format(module, function))(tainted_string)
    line, hash_value = get_line_and_hash(path, VULN_PATH_TRAVERSAL, filename=FIXTURES_PATH)

    data = _get_iast_data()
    vulnerability = data["vulnerabilities"][0]
    assert len(data["vulnerabilities"]) == 1
    assert vulnerability["type"] == VULN_PATH_TRAVERSAL
    assert vulnerability["location"]["path"] == FIXTURES_PATH
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == path
    assert "class" not in vulnerability["location"]
    assert vulnerability["hash"] == hash_value
    assert vulnerability["evidence"]["valueParts"] == [{"source": 0, "value": file_path}]
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_path_traversal_deduplication(num_vuln_expected, iast_context_deduplication_enabled, ensure_test_file):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.path_traversal")
    file_path = ensure_test_file

    tainted_string = taint_pyobject(
        file_path, source_name="path", source_value=file_path, source_origin=OriginType.PATH
    )

    for _ in range(0, 5):
        mod.pt_open(tainted_string)

    span_report = get_iast_reporter()

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected


@pytest.mark.parametrize("non_text_obj,obj_type", NON_TEXT_TYPES_TEST_DATA)
def test_path_traversal_non_text_types_no_vulnerability(non_text_obj, obj_type, iast_context_defaults):
    """Test that non-text types don't trigger path traversal vulnerabilities."""
    # Taint the non-text object
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # Call the path traversal reporting function directly
    check_and_report_path_traversal(tainted_obj)

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, f"Vulnerability reported for {obj_type}: {non_text_obj}"
