from copy import copy
import os
import subprocess  # nosec
import sys

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.taint_sinks.command_injection import patch
from ddtrace.appsec._iast.taint_sinks.command_injection import unpatch
from ddtrace.internal import core
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_global_config


FIXTURES_PATH = "tests/appsec/iast/taint_sinks/test_command_injection.py"

_PARAMS = ["/bin/ls", "-l"]


@pytest.fixture(autouse=True)
def auto_unpatch():
    yield
    try:
        unpatch()
    except AttributeError:
        pass


def setup():
    oce._enabled = True


def test_ossystem(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "mytest/folder/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
        )
        assert is_pyobject_tainted(_BAD_DIR)
        with tracer.trace("ossystem_test"):
            # label test_ossystem
            os.system(add_aspect("dir -l ", _BAD_DIR))

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()
        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]
        assert "value" not in vulnerability["evidence"].keys()
        assert vulnerability["evidence"].get("pattern") is None
        assert vulnerability["evidence"].get("redacted") is None
        assert source["name"] == "test_ossystem"
        assert source["origin"] == OriginType.PARAMETER
        assert "value" not in source.keys()

        line, hash_value = get_line_and_hash("test_ossystem", VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


def test_communicate(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_communicate",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            # label test_communicate
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
            subp.communicate()
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]
        assert "value" not in vulnerability["evidence"].keys()
        assert "pattern" not in vulnerability["evidence"].keys()
        assert "redacted" not in vulnerability["evidence"].keys()
        assert source["name"] == "test_communicate"
        assert source["origin"] == OriginType.PARAMETER
        assert "value" not in source.keys()

        line, hash_value = get_line_and_hash("test_communicate", VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


def test_run(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_run",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            # label test_run
            subprocess.run(["dir", "-l", _BAD_DIR])

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]
        assert "value" not in vulnerability["evidence"].keys()
        assert "pattern" not in vulnerability["evidence"].keys()
        assert "redacted" not in vulnerability["evidence"].keys()
        assert source["name"] == "test_run"
        assert source["origin"] == OriginType.PARAMETER
        assert "value" not in source.keys()

        line, hash_value = get_line_and_hash("test_run", VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


def test_popen_wait(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_popen_wait",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            # label test_popen_wait
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]
        assert "value" not in vulnerability["evidence"].keys()
        assert "pattern" not in vulnerability["evidence"].keys()
        assert "redacted" not in vulnerability["evidence"].keys()
        assert source["name"] == "test_popen_wait"
        assert source["origin"] == OriginType.PARAMETER
        assert "value" not in source.keys()

        line, hash_value = get_line_and_hash("test_popen_wait", VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


def test_popen_wait_shell_true(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_popen_wait_shell_true",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            # label test_popen_wait_shell_true
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR], shell=True)
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
            {"pattern": "abcdefghijklmn", "redacted": True, "source": 0},
        ]
        assert "value" not in vulnerability["evidence"].keys()
        assert "pattern" not in vulnerability["evidence"].keys()
        assert "redacted" not in vulnerability["evidence"].keys()
        assert source["name"] == "test_popen_wait_shell_true"
        assert source["origin"] == OriginType.PARAMETER
        assert "value" not in source.keys()

        line, hash_value = get_line_and_hash("test_popen_wait_shell_true", VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
@pytest.mark.parametrize(
    "function,mode,arguments, tag",
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
def test_osspawn_variants(tracer, iast_span_defaults, function, mode, arguments, tag):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_osspawn_variants",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        copied_args = copy(arguments)
        copied_args.append(_BAD_DIR)

        if "_" in function.__name__:
            # wrapt changes function names when debugging
            cleaned_name = function.__name__.split("_")[-1]
        else:
            cleaned_name = function.__name__

        with tracer.trace("osspawn_test"):
            if "spawnv" in cleaned_name:
                # label test_osspawn_variants2
                function(mode, copied_args[0], copied_args)
            else:
                # label test_osspawn_variants1
                function(mode, copied_args[0], *copied_args)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        vulnerability = data["vulnerabilities"][0]
        source = data["sources"][0]
        assert vulnerability["type"] == VULN_CMDI
        assert vulnerability["evidence"]["valueParts"] == [{"value": "/bin/ls -l "}, {"source": 0, "value": _BAD_DIR}]
        assert "value" not in vulnerability["evidence"].keys()
        assert "pattern" not in vulnerability["evidence"].keys()
        assert "redacted" not in vulnerability["evidence"].keys()
        assert source["name"] == "test_osspawn_variants"
        assert source["origin"] == OriginType.PARAMETER
        assert source["value"] == _BAD_DIR

        line, hash_value = get_line_and_hash(tag, VULN_CMDI, filename=FIXTURES_PATH)
        assert vulnerability["location"]["path"] == FIXTURES_PATH
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
def test_multiple_cmdi(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        _BAD_DIR = taint_pyobject(
            pyobject="forbidden_dir/",
            source_name="test_run",
            source_value="forbidden_dir/",
            source_origin=OriginType.PARAMETER,
        )
        dir_2 = taint_pyobject(
            pyobject="qwerty/",
            source_name="test_run",
            source_value="qwerty/",
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("test_multiple_cmdi"):
            subprocess.run(["dir", "-l", _BAD_DIR])
            subprocess.run(["dir", "-l", dir_2])

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        assert len(list(data["vulnerabilities"])) == 2


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
def test_string_cmdi(tracer, iast_span_defaults):
    with override_global_config(dict(_iast_enabled=True)):
        patch()
        cmd = taint_pyobject(
            pyobject="dir -l .",
            source_name="test_run",
            source_value="dir -l .",
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("test_string_cmdi"):
            subprocess.run(cmd, shell=True, check=True)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        data = span_report.build_and_scrub_value_parts()

        assert len(list(data["vulnerabilities"])) == 1


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
def test_cmdi_deduplication(num_vuln_expected, tracer, iast_span_deduplication_enabled):
    patch()
    _BAD_DIR = "forbidden_dir/"
    _BAD_DIR = taint_pyobject(
        pyobject=_BAD_DIR,
        source_name="test_ossystem",
        source_value=_BAD_DIR,
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(_BAD_DIR)
    for _ in range(0, 5):
        with tracer.trace("ossystem_test"):
            # label test_ossystem
            os.system(add_aspect("dir -l ", _BAD_DIR))

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report
        data = span_report.build_and_scrub_value_parts()
        assert len(data["vulnerabilities"]) == num_vuln_expected
