import os
import subprocess

import pytest
from ddtrace.internal import _context

from ddtrace import Pin, patch_all
from ddtrace.ext import SpanTypes
from tests.utils import DummyTracer, override_global_config


# JJJ test truncated
# JJJ test scrub_arg
# JJJ test scrub ENV_VAR=foo cmd
# JJJ test allowed env vars
# JJJ test forbidden commands
# JJJ test parse_shell_args
# JJJ test parse_exec_args

def test_ossystem(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        with tracer.trace("os.system", span_type=SpanTypes.SYSTEM):
            ret = os.system("ls -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.get_tag("name") == "command_execution"
        assert span.get_tag("cmd.shell") == str(["ls", "-l", "/"])
        assert span.get_tag("cmd.exit_code") == "0"
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "os"
        assert span.get_tag("resource") == "ls"


def test_ossystem_noappsec(tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        patch_all()
        assert Pin.get_from(os) is None


def test_ospopen(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        with tracer.trace("os.popen", span_type=SpanTypes.SYSTEM):
            pipe = os.popen("ls -li /")
            content = pipe.read()
            assert content
            assert pipe.close() is None

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.get_tag("name") == "command_execution"
        assert span.get_tag("cmd.shell") == str(["ls", "-li", "/"])
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "os"
        assert span.get_tag("resource") == "ls"


# XXX JJJ only linux!
# XXX factorize command, return value, params into a fixture

_PARAMS = ["/usr/bin/ls", "-l", "/"]
_PARAMS_ENV = _PARAMS + [{"fooenv": "bar"}]  # type: ignore
@pytest.mark.parametrize(
    "function,mode,arguments",
    [
        (os.spawnl, os.P_WAIT, _PARAMS),
        (os.spawnl, os.P_NOWAIT, _PARAMS),
        (os.spawnle, os.P_WAIT, _PARAMS_ENV),
        (os.spawnle, os.P_NOWAIT, _PARAMS_ENV),
        (os.spawnlp, os.P_WAIT, _PARAMS),
        (os.spawnlp, os.P_NOWAIT, _PARAMS),
        (os.spawnlpe, os.P_WAIT, _PARAMS_ENV),
        (os.spawnlpe, os.P_NOWAIT, _PARAMS_ENV),
        (os.spawnv, os.P_WAIT, _PARAMS),
        (os.spawnv, os.P_NOWAIT, _PARAMS),
        (os.spawnve, os.P_WAIT, _PARAMS_ENV),
        (os.spawnve, os.P_NOWAIT, _PARAMS_ENV),
        (os.spawnvp, os.P_WAIT, _PARAMS),
        (os.spawnvp, os.P_NOWAIT, _PARAMS),
        (os.spawnvpe, os.P_WAIT, _PARAMS_ENV),
        (os.spawnvpe, os.P_NOWAIT, _PARAMS_ENV),
    ],
)
def test_osspawn_variants(tracer, function, mode, arguments):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(os).clone(tracer=tracer).onto(os)

        if "_" in function.__name__:
            # wrapt changes function names when debugging
            cleaned_name = function.__name__.split("_")[-1]
        else:
            cleaned_name = function.__name__


        with tracer.trace("os.spawn", span_type=SpanTypes.SYSTEM):
            if "spawnv" in cleaned_name:
                if "e" in cleaned_name:
                    ret = function(mode, arguments[0], arguments[:-1], arguments[-1])
                else:
                    ret = function(mode, arguments[0], arguments)
            else:
                ret = function(mode, arguments[0], *arguments)
            if mode == os.P_WAIT:
                assert ret == 0
            else:
                assert ret > 0  # for P_NOWAIT returned value is the pid

        spans = tracer.pop()
        assert spans
        # assert len(spans) > 1
        span = spans[1]
        if mode == os.P_WAIT:
            assert span.get_tag("cmd.exit_code") == str(ret)

        assert span.get_tag("name") == "command_execution"
        assert span.get_tag("resource") == arguments[0]
        param_arguments = arguments[1:-1] if "e" in cleaned_name else arguments[1:]
        assert span.get_tag("cmd.exec") == arguments[0] + " " + " ".join(param_arguments)
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "os"


def test_subprocess_init_shell_true(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["ls", "-li", "/"], shell = True)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.get_tag("name") == "command_execution"
        assert not span.get_tag("cmd.exec")
        assert span.get_tag("cmd.shell") == "ls -li /"
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("resource") == "ls"


def test_subprocess_init_shell_false(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess.Popen).clone(tracer=tracer).onto(subprocess.Popen)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["ls", "-li", "/"], shell = False)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert not span.get_tag("cmd.shell")
        assert span.get_tag("cmd.exec") == "ls -li /"


def test_subprocess_wait_shell_false(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
            subp = subprocess.Popen(args=["ls", "-li", "/"], shell=False)
            subp.wait()

            assert not _context.get_item("subprocess_popen_is_shell", span=span)
            assert _context.get_item("subprocess_popen_truncated", span=span) == "false"
            assert _context.get_item("subprocess_popen_args", span=span) == ["ls", "-li", "/"]


def test_subprocess_wait_shell_true(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
            subp = subprocess.Popen(args=["ls", "-li", "/"], shell=True)
            subp.wait()

            assert _context.get_item("subprocess_popen_is_shell", span=span)


def test_subprocess_run(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM):
            result = subprocess.run(["ls", "-l", "/"], shell=True)
            assert result.returncode == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.get_tag("name") == "command_execution"
        assert not span.get_tag("cmd.exec")
        assert span.get_tag("cmd.shell") == "ls -l /"
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("cmd.exit_code") == "0"
        assert span.get_tag("resource") == "ls"



def test_subprocess_communicate(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(args=["ls", "-li", "/"], shell=True)
            subp.communicate()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.get_tag("name") == "command_execution"
        assert not span.get_tag("cmd.exec")
        assert span.get_tag("cmd.shell") == "ls -li /"
        assert span.get_tag("cmd.truncated") == "false"
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("cmd.exit_code") == "0"
        assert span.get_tag("resource") == "ls"
