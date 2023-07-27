from copy import copy
import os
import subprocess
import sys

import pytest

from ddtrace import Pin
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast._ast.aspects import add_aspect
from ddtrace.appsec.iast._taint_tracking import OriginType  # noqa: F401
from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.appsec.iast.constants import VULN_CMDI
from ddtrace.contrib.subprocess.patch import SubprocessCmdLine
from ddtrace.contrib.subprocess.patch import patch
from ddtrace.contrib.subprocess.patch import unpatch
from ddtrace.internal import core
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def auto_unpatch():
    SubprocessCmdLine._clear_cache()
    yield
    SubprocessCmdLine._clear_cache()
    try:
        unpatch()
    except AttributeError:
        # Tests with appsec disabled or that didn't patch
        pass


def test_ossystem(tracer, iast_span_defaults):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("ossystem_test"):
            os.system(add_aspect("dir -l ", _BAD_DIR))

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        assert list(span_report.vulnerabilities)[0].location.line == 45
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["dir", "-l", _BAD_DIR]


def test_communicate(tracer, iast_span_defaults):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
            subp.communicate()
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        assert list(span_report.vulnerabilities)[0].location.line == 69
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["dir", "-l", _BAD_DIR]


def test_run(tracer, iast_span_defaults):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            subprocess.run(["dir", "-l", _BAD_DIR])

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        assert list(span_report.vulnerabilities)[0].location.line == 95
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["dir", "-l", _BAD_DIR]


def test_popen_wait(tracer, iast_span_defaults):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR])
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        assert list(span_report.vulnerabilities)[0].location.line == 119
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["dir", "-l", _BAD_DIR]


def test_popen_wait_shell_true(tracer, iast_span_defaults):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
            source_value=_BAD_DIR,
            source_origin=OriginType.PARAMETER,
        )
        with tracer.trace("communicate_test"):
            subp = subprocess.Popen(args=["dir", "-l", _BAD_DIR], shell=True)
            subp.wait()

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        assert list(span_report.vulnerabilities)[0].location.line == 144
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["dir", "-l", _BAD_DIR]


_PARAMS = ["/bin/ls", "-l"]


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
@pytest.mark.parametrize(
    "function,mode,arguments",
    [
        (os.spawnl, os.P_WAIT, _PARAMS),
        (os.spawnl, os.P_NOWAIT, _PARAMS),
        (os.spawnlp, os.P_WAIT, _PARAMS),
        (os.spawnlp, os.P_NOWAIT, _PARAMS),
        (os.spawnv, os.P_WAIT, _PARAMS),
        (os.spawnv, os.P_NOWAIT, _PARAMS),
        (os.spawnvp, os.P_WAIT, _PARAMS),
        (os.spawnvp, os.P_NOWAIT, _PARAMS),
    ],
)
def test_osspawn_variants(tracer, iast_span_defaults, function, mode, arguments):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        patch()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        _BAD_DIR = "forbidden_dir/"
        _BAD_DIR = taint_pyobject(
            pyobject=_BAD_DIR,
            source_name="test_ossystem",
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
                function(mode, copied_args[0], copied_args)
            else:
                function(mode, copied_args[0], *copied_args)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report
        assert list(span_report.vulnerabilities)[0].type == VULN_CMDI
        assert list(span_report.vulnerabilities)[0].location.path == "test_command_injection.py"
        # FIXME: update this when the valueParts fixes from the scrubbing PR are merged
        assert list(span_report.vulnerabilities)[0].evidence.valueParts == ["/bin/ls", "-l", _BAD_DIR]
