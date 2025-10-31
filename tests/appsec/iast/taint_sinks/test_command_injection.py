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
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.secure_marks import cmdi_sanitizer
from ddtrace.appsec._iast.taint_sinks.command_injection import _iast_report_cmdi
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks._taint_sinks_utils import NON_TEXT_TYPES_TEST_DATA


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/command_injection.py"

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
    source_name = "pt_os_system"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
    )
    assert is_pyobject_tainted(_BAD_DIR)
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    mod.pt_os_system("dir -l ", _BAD_DIR)
    _assert_vulnerability("pt_os_system", source_name=source_name)


def test_subprocess_popen(iast_context_defaults):
    source_name = "pt_subprocess_popen"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    mod.pt_subprocess_popen(["dir", "-l", _BAD_DIR])
    _assert_vulnerability("pt_subprocess_popen", source_name=source_name)


def test_run(iast_context_defaults):
    source_name = "pt_subprocess_run"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    mod.pt_subprocess_run(["dir", "-l", _BAD_DIR])
    _assert_vulnerability("pt_subprocess_run", source_name=source_name)


def test_popen_wait_shell_true(iast_context_defaults):
    source_name = "pt_subprocess_popen_shell"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=source_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    # label pt_subprocess_popen_shell
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    mod.pt_subprocess_popen_shell(["dir", "-l", _BAD_DIR])

    _assert_vulnerability("pt_subprocess_popen_shell", source_name=source_name)


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
@pytest.mark.parametrize(
    "function,mode,arguments,tag",
    [
        ("spawnl", os.P_WAIT, _PARAMS, "test_osspawn_variants1"),
        ("spawnl", os.P_NOWAIT, _PARAMS, "test_osspawn_variants1"),
        ("spawnlp", os.P_WAIT, _PARAMS, "test_osspawn_variants1"),
        ("spawnlp", os.P_NOWAIT, _PARAMS, "test_osspawn_variants1"),
        ("spawnv", os.P_WAIT, _PARAMS, "test_osspawn_variants2"),
        ("spawnv", os.P_NOWAIT, _PARAMS, "test_osspawn_variants2"),
        ("spawnvp", os.P_WAIT, _PARAMS, "test_osspawn_variants2"),
        ("spawnvp", os.P_NOWAIT, _PARAMS, "test_osspawn_variants2"),
    ],
)
def test_osspawn_variants(iast_context_defaults, function, mode, arguments, tag):
    func_name = "pt_" + function
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR_DEFAULT,
        source_name=func_name,
        source_value=_BAD_DIR_DEFAULT,
        source_origin=OriginType.PARAMETER,
    )
    copied_args = copy(arguments)
    copied_args.append(_BAD_DIR)
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    if "spawnv" in func_name:
        getattr(mod, func_name)(mode, copied_args[0], copied_args[1:])
    else:
        getattr(mod, func_name)(mode, copied_args[0], *copied_args[1:])

    _assert_vulnerability(
        func_name,
        value_parts=[{"value": "/bin/ls -l "}, {"source": 0, "value": _BAD_DIR}],
        source_name=func_name,
        check_value=True,
        function=func_name,
    )


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
def test_multiple_cmdi(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
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
    mod.pt_subprocess_run(["dir", "-l", _BAD_DIR])
    mod.pt_subprocess_run(["dir", "-l", dir_2])

    data = _get_iast_data()

    assert len(list(data["vulnerabilities"])) == 2


@pytest.mark.skipif(sys.platform not in ["linux", "darwin"], reason="Only for Unix")
def test_string_cmdi(iast_context_defaults):
    source_name = "pt_subprocess_popen"
    tainted_cmd = taint_pyobject(
        pyobject="dir -l .",
        source_name=source_name,
        source_value="dir -l .",
        source_origin=OriginType.PARAMETER,
    )
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")
    with pytest.raises(FileNotFoundError):
        subprocess.Popen(tainted_cmd)

    with pytest.raises(FileNotFoundError):
        mod.pt_subprocess_popen(tainted_cmd)

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

    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")

    with pytest.raises(FileNotFoundError):
        mod.pt_subprocess_popen(result)

    # Verify the result is marked as secure
    span_report = get_iast_reporter()
    assert span_report is None


def test_cmdi_deduplication(iast_context_deduplication_enabled):
    _end_iast_context_and_oce()
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.command_injection")

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

            mod.pt_os_system("dir -l ", _BAD_DIR)

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
    _iast_report_cmdi("func_name", tainted_obj)

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
    _iast_report_cmdi("func_name", tainted_list)

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
