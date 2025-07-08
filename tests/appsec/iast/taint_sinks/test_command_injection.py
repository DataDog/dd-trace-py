from copy import copy
import os
import subprocess  # nosec
import sys
from unittest import mock

import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.secure_marks import cmdi_sanitizer
from ddtrace.appsec._iast.taint_sinks.command_injection import _iast_report_cmdi
from ddtrace.appsec._iast.taint_sinks.command_injection import patch
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks._taint_sinks_utils import NON_TEXT_TYPES_TEST_DATA


FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_command_injection.py"

_PARAMS = ["/bin/ls", "-l"]

_BAD_DIR_DEFAULT = "forbidden_dir/"


def _assert_vulnerability(label, value_parts=None, source_name="", check_value=False, function=None, class_name=None):
    function_name = label if not function else function
    if value_parts is None:
        value_parts = [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]

    data = _get_iast_data()
    vulnerability = data["vulnerabilities"][0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_CMDI
    assert vulnerability["evidence"]["valueParts"] == value_parts
    assert "value" not in vulnerability["evidence"].keys()
    assert vulnerability["evidence"].get("pattern") is None
    assert vulnerability["evidence"].get("redacted") is None
    assert source["name"] == source_name
    assert source["origin"] == OriginType.PARAMETER
    if check_value:
        assert source["value"] == _BAD_DIR_DEFAULT
    else:
        assert "value" not in source.keys()

    line, hash_value = get_line_and_hash(label, VULN_CMDI, filename=FIXTURES_PATH)
    assert vulnerability["location"]["path"] == FIXTURES_PATH
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == function_name
    assert vulnerability["location"].get("class") == class_name
    assert vulnerability["hash"] == hash_value


def test_ossystem(iast_context_defaults):
    source_name = "test_ossystem"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
    )
    assert is_pyobject_tainted(_BAD_DIR)
    # label test_ossystem
    os.system(add_aspect("dir -l ", _BAD_DIR))
    _assert_vulnerability("test_ossystem", source_name=source_name)


def test_communicate(iast_context_defaults):
    source_name = "test_communicate"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    # label test_communicate
    subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
    subp.communicate()
    subp.wait()
    _assert_vulnerability("test_communicate", source_name=source_name)


def test_run(iast_context_defaults):
    source_name = "test_run"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    # label test_run
    subprocess.run(["dir", "-l", _BAD_DIR])
    _assert_vulnerability("test_run", source_name=source_name)


def test_popen_wait(iast_context_defaults):
    source_name = "test_popen_wait"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    # label test_popen_wait
    subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
    subp.wait()

    _assert_vulnerability("test_popen_wait", source_name=source_name)


def test_popen_wait_shell_true(iast_context_defaults):
    source_name = "test_popen_wait_shell_true"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    # label test_popen_wait_shell_true
    subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR], shell=True)
    subp.wait()

    _assert_vulnerability("test_popen_wait_shell_true", source_name=source_name)


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
@pytest.mark.parametrize(
    "function,mode,arguments,tag",
    [
        (os.spawnl, os.P_WAIT, _PARAMS, "test_osspawn_variants1"),
        (os.spawnl, os.P_NOWAIT, _PARAMS, "test_osspawn_variants1"),
        (os.spawnlp, os.P_WAIT, _PARAMS, "test_osspawn_variants1"),
        (os.spawnlp, os.P_NOWAIT, _PARAMS, "test_osspawn_variants1"),
        (os.spawnv, os.P_WAIT, _PARAMS, "test_osspawn_variants2"),
        (os.spawnv, os.P_NOWAIT, _PARAMS, "test_osspawn_variants2"),
        (os.spawnvp, os.P_WAIT, _PARAMS, "test_osspawn_variants2"),
        (os.spawnvp, os.P_NOWAIT, _PARAMS, "test_osspawn_variants2"),
    ],
)
def test_osspawn_variants(iast_context_defaults, function, mode, arguments, tag):
    source_name = "test_osspawn_variants"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    copied_args = copy(arguments)
    copied_args.append(_BAD_DIR)

    if "_" in function.__name__:
        # wrapt changes function names when debugging
        cleaned_name = function.__name__.split("_")[-1]
    else:
        cleaned_name = function.__name__

    if "spawnv" in cleaned_name:
        # label test_osspawn_variants2
        function(mode, copied_args[0], copied_args[1:])
        label = "test_osspawn_variants2"
    else:
        # label test_osspawn_variants1
        function(mode, copied_args[0], *copied_args[1:])
        label = "test_osspawn_variants1"

    _assert_vulnerability(
        label,
        value_parts=[{"value": "/bin/ls -l "}, {"source": 0, "value": _BAD_DIR}],
        source_name=source_name,
        check_value=True,
        function="test_osspawn_variants",
    )


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
def test_multiple_cmdi(iast_context_defaults):
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name="test_run",
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    dir_2 = taint_pyobject(
        pyobject="qwerty/",
        source_name="test_run",
        source_value="qwerty/",
        source_origin=OriginType.PARAMETER,
    )
    subprocess.run(["dir", "-l", _BAD_DIR])
    subprocess.run(["dir", "-l", dir_2])

    data = _get_iast_data()

    assert len(list(data["vulnerabilities"])) == 2


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
def test_string_cmdi(iast_context_defaults):
    cmd = taint_pyobject(
        pyobject="dir -l .",
        source_name="test_run",
        source_value="dir -l .",
        source_origin=OriginType.PARAMETER,
    )
    subprocess.run(cmd, shell=True, check=True)

    data = _get_iast_data()

    assert len(list(data["vulnerabilities"])) == 1


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
def test_string_cmdi_secure_mark(iast_context_defaults):
    cmd = taint_pyobject(
        pyobject="dir -l .",
        source_name="test_run",
        source_value="dir -l .",
        source_origin=OriginType.PARAMETER,
    )

    # Mock the quote function
    cmd_function = mock.Mock(return_value=cmd)

    # Apply the sanitizer
    result = cmdi_sanitizer(cmd_function, None, [cmd], {})

    subprocess.run(result, shell=True, check=True)

    # Verify the result is marked as secure
    span_report = get_iast_reporter()
    assert span_report is None


def test_cmdi_deduplication(iast_context_deduplication_enabled):
    patch()
    _end_iast_context_and_oce()
    for num_vuln_expected in [1, 0, 0]:
        _start_iast_context_and_oce()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        assert is_pyobject_tainted(_BAD_DIR)
        for _ in range(0, 5):
            # label test_ossystem
            os.system(add_aspect("dir -l ", _BAD_DIR))

        span_report = get_iast_reporter()

        if num_vuln_expected == 0:
            assert span_report is None
        else:
            assert span_report
            data = span_report.build_and_scrub_value_parts()
            assert len(data["vulnerabilities"]) == num_vuln_expected
        _end_iast_context_and_oce()


@pytest.mark.parametrize("non_text_obj,obj_type", NON_TEXT_TYPES_TEST_DATA)
def test_cmdi_non_text_types_no_vulnerability(non_text_obj, obj_type, iast_context_defaults):
    """Test that non-text types don't trigger command injection vulnerabilities."""
    # Taint the non-text object (this should not cause a vulnerability report)
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # Call the command injection reporting function directly
    _iast_report_cmdi(tainted_obj)

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, f"Vulnerability reported for {obj_type}: {non_text_obj}"


def test_cmdi_list_with_non_text_types_no_vulnerability(iast_context_defaults):
    """Test that lists containing non-text types don't trigger vulnerabilities."""
    # Create a list with mixed non-text types
    mixed_list = [123, 456.78, True, None]

    # Taint individual elements
    tainted_list = []
    for item in mixed_list:
        tainted_item = taint_pyobject(
            item,
            source_name="test_source",
            source_value=str(item),
            source_origin=OriginType.PARAMETER,
        )
        tainted_list.append(tainted_item)

    # Call the command injection reporting function
    _iast_report_cmdi(tainted_list)

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, "Vulnerability reported for list with non-text types"


def test_cmdi_integration_with_subprocess(iast_context_defaults):
    """Test command injection with subprocess using non-text types."""
    non_text_obj = 12345
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # This should not trigger a vulnerability report
    with mock.patch("subprocess.run"):
        try:
            subprocess.run(["echo", tainted_obj])
        except (TypeError, ValueError):
            # Expected since subprocess.run expects strings
            pass

    span_report = get_iast_reporter()
    assert span_report is None, "Vulnerability reported for non-text type in subprocess"
