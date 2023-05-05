import os
import subprocess

import pytest

from ddtrace import Pin
from ddtrace import patch_all
from ddtrace.appsec._patch_subprocess_executions import SubprocessCmdLine
from ddtrace.appsec._patch_subprocess_executions import _unpatch
from ddtrace.internal.compat import PY2, PY3
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from tests.utils import override_global_config


# JJJ use some command that can work also on Windoze


@pytest.fixture(autouse=True)
def auto_unpatch():
    SubprocessCmdLine._clear_cache()
    yield
    SubprocessCmdLine._clear_cache()
    try:
        _unpatch()
    except AttributeError:
        # Tests with appsec disabled or that didn't patch
        pass


allowed_envvars_fixture_list = []
for allowed in SubprocessCmdLine.ENV_VARS_ALLOWLIST:
    allowed_envvars_fixture_list.extend(
        [
            (
                SubprocessCmdLine(["%s=bar" % allowed, "BAR=baz", "ls", "-li", "/", "OTHER=any"], True, shell=True),
                ["%s=bar" % allowed, "BAR=?", "ls", "-li", "/", "OTHER=any"],
                ["%s=bar" % allowed, "BAR=?"],
                "ls",
                ["-li", "/", "OTHER=any"],
            ),
            (
                SubprocessCmdLine(
                    ["FOO=bar", "%s=bar" % allowed, "BAR=baz", "ls", "-li", "/", "OTHER=any"], True, shell=True
                ),
                ["FOO=?", "%s=bar" % allowed, "BAR=?", "ls", "-li", "/", "OTHER=any"],
                ["FOO=?", "%s=bar" % allowed, "BAR=?"],
                "ls",
                ["-li", "/", "OTHER=any"],
            ),
        ]
    )


@pytest.mark.parametrize(
    "cmdline_obj,full_list,env_vars,binary,arguments",
    [
        (
            SubprocessCmdLine(["FOO=bar", "BAR=baz", "ls", "-li", "/"], True, shell=True),
            ["FOO=?", "BAR=?", "ls", "-li", "/"],
            ["FOO=?", "BAR=?"],
            "ls",
            ["-li", "/"],
        ),
        (
            SubprocessCmdLine(["FOO=bar", "BAR=baz", "ls", "-li", "/dir with spaces", "OTHER=any"], True, shell=True),
            ["FOO=?", "BAR=?", "ls", "-li", "/dir with spaces", "OTHER=any"],
            ["FOO=?", "BAR=?"],
            "ls",
            ["-li", "/dir with spaces", "OTHER=any"],
        ),
        (
            SubprocessCmdLine(["FOO=bar", "lower=baz", "ls", "-li", "/", "OTHER=any"], True, shell=True),
            ["FOO=?", "lower=baz", "ls", "-li", "/", "OTHER=any"],
            ["FOO=?"],
            "lower=baz",
            ["ls", "-li", "/", "OTHER=any"],
        ),
    ]
    + allowed_envvars_fixture_list,
)
def test_shellcmdline(cmdline_obj, full_list, env_vars, binary, arguments):
    assert cmdline_obj.as_list() == full_list
    assert cmdline_obj.env_vars == env_vars
    assert cmdline_obj.binary == binary
    assert cmdline_obj.arguments == arguments


denied_binaries_fixture_list = []
for denied in SubprocessCmdLine.BINARIES_DENYLIST:
    denied_binaries_fixture_list.extend(
        [
            (
                SubprocessCmdLine([denied, "-foo", "bar", "baz"], True),
                [denied, "?", "?", "?"],
                ["?", "?", "?"],
            )
        ]
    )


@pytest.mark.parametrize(
    "cmdline_obj,full_list,arguments", [(SubprocessCmdLine(["ls", "-li", "/"], True), ["ls", "-li", "/"], ["-li", "/"])]
)
def test_binary_arg_scrubbing(cmdline_obj, full_list, arguments):
    assert cmdline_obj.as_list() == full_list
    assert cmdline_obj.arguments == arguments


@pytest.mark.parametrize(
    "cmdline_obj,arguments",
    [
        (
            SubprocessCmdLine(
                ["binary", "-a", "-b1", "-c=foo", "-d bar", "--long1=long1value", "--long2 long2value"],
                as_list=True,
                shell=False,
            ),
            ["-a", "-b1", "-c=foo", "-d bar", "--long1=long1value", "--long2 long2value"],
        ),
        (
            SubprocessCmdLine(
                [
                    "binary",
                    "-a",
                    "-passwd=SCRUB",
                    "-passwd",
                    "SCRUB",
                    "-d bar",
                    "--apikey=SCRUB",
                    "-efoo",
                    "/auth_tokenSCRUB",
                    "--secretSCRUB",
                ],
                as_list=True,
                shell=False,
            ),
            ["-a", "?", "-passwd", "?", "-d bar", "?", "-efoo", "?", "?"],
        ),
    ],
)
def test_argument_scrubing(cmdline_obj, arguments):
    assert cmdline_obj.arguments == arguments


@pytest.mark.parametrize(
    "cmdline_obj,expected_str,expected_list,truncated",
    [
        (
            SubprocessCmdLine(["ls", "-A" + "loremipsum" * 40, "-B"], as_list=True),
            'ls -Aloremipsumloremipsumloremipsumlo "4kB argument truncated by 328 characters"',
            ["ls", "-Aloremipsumloremipsumloremipsumlo", "4kB argument truncated by 328 characters"],
            True,
        ),
        (SubprocessCmdLine("ls -A -B -C", as_list=False), "ls -A -B -C", ["ls", "-A", "-B", "-C"], False),
    ],
)
def test_truncation(cmdline_obj, expected_str, expected_list, truncated):
    orig_limit = SubprocessCmdLine.TRUNCATE_LIMIT
    limit = 80
    try:
        SubprocessCmdLine.TRUNCATE_LIMIT = limit

        res_str = cmdline_obj.as_string()
        assert res_str == expected_str

        res_list = cmdline_obj.as_list()
        assert res_list == expected_list

        assert cmdline_obj.truncated == truncated

        if cmdline_obj.truncated:
            assert len(res_str) == limit
    finally:
        SubprocessCmdLine.TRUNCATE_LIMIT = orig_limit


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
        assert span.get_tag("cmd.shell") == "ls -l /"
        assert span.get_tag("cmd.exit_code") == "0"
        assert not span.get_tag("cmd.truncated")
        assert span.get_tag("component") == "os"
        assert span.get_tag("resource") == "ls"


def test_unpatch(tracer):
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
        assert span.get_tag("cmd.shell") == "ls -l /"

    _unpatch()
    with override_global_config(dict(_appsec_enabled=True)):
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        with tracer.trace("os.system_unpatch", span_type=SpanTypes.SYSTEM):
            ret = os.system("ls -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) == 1
        span = spans[0]
        assert not span.get_tag("cmd.shell")
        assert not span.get_tag("name")
        assert not span.get_tag("cmd.shell")
        assert not span.get_tag("cmd.exit_code")
        assert not span.get_tag("component")
        assert not span.get_tag("resource")


def test_ossystem_noappsec(tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        patch_all()
        assert not hasattr(os.system, "__wrapped__")
        assert not hasattr(os._spawnvef, "__wrapped__")
        assert not hasattr(subprocess.Popen.__init__, "__wrapped__")


@pytest.mark.skipif(PY2, reason="Python3 specific test (pins into subprocess)")
def test_py3ospopen(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("os.popen", span_type=SpanTypes.SYSTEM):
            pipe = os.popen("ls -li /")
            content = pipe.read()
            assert content
            pipe.close()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.get_tag("name") == "command_execution"
        assert span.get_tag("cmd.shell") == "ls -li /"
        assert not span.get_tag("cmd.truncated")
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("resource") == "ls"


@pytest.mark.skipif(PY3, reason="Python2 specific tests")
def test_py2ospopen(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(os).clone(tracer=tracer).onto(os)
        for func in [os.popen, os.popen2, os.popen3]:
            with tracer.trace("os.popen", span_type=SpanTypes.SYSTEM):
                res = func("ls -li %s" % func.__name__)
                assert res
                readpipe = res[0] if isinstance(res, tuple) else res
                readpipe.close()

            spans = tracer.pop()
            assert spans
            assert len(spans) > 1
            span = spans[1]
            assert span.get_tag("name") == "command_execution"
            assert span.get_tag("cmd.exec") == str(["ls", "-li", func.__name__])
            assert not span.get_tag("cmd.truncated")
            assert span.get_tag("component") == "os"
            assert span.get_tag("resource") == "ls"


# JJJ only linux!

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
        assert span.get_tag("cmd.exec") == str([arguments[0]] + param_arguments)
        assert not span.get_tag("cmd.truncated")
        assert span.get_tag("component") == "os"


def test_subprocess_init_shell_true(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["ls", "-li", "/"], shell=True)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.get_tag("name") == "command_execution"
        assert not span.get_tag("cmd.exec")
        assert span.get_tag("cmd.shell") == "ls -li /"
        assert not span.get_tag("cmd.truncated")
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("resource") == "ls"


def test_subprocess_init_shell_false(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["ls", "-li", "/"], shell=False)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert not span.get_tag("cmd.shell")
        assert span.get_tag("cmd.exec") == "['ls', '-li', '/']"


def test_subprocess_wait_shell_false(tracer):
    args = ["ls", "-li", "/"]
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
            subp = subprocess.Popen(args=args, shell=False)
            subp.wait()

            assert not _context.get_item("subprocess_popen_is_shell", span=span)
            assert not _context.get_item("subprocess_popen_truncated", span=span)
            assert _context.get_item("subprocess_popen_line", span=span) == args


def test_subprocess_wait_shell_true(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        patch_all()
        Pin.get_from(subprocess).clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
            subp = subprocess.Popen(args=["ls", "-li", "/"], shell=True)
            subp.wait()

            assert _context.get_item("subprocess_popen_is_shell", span=span)


@pytest.mark.skipif(PY2, reason="Python2 does not have subprocess.run")
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
        assert not span.get_tag("cmd.truncated")
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
        assert not span.get_tag("cmd.truncated")
        assert span.get_tag("component") == "subprocess"
        assert span.get_tag("cmd.exit_code") == "0"
        assert span.get_tag("resource") == "ls"


def test_cache_hit():
    cmd1 = SubprocessCmdLine("ls -foo -bar", shell=False)
    cmd2 = SubprocessCmdLine("ls -foo -bar", shell=False)
    cmd3 = SubprocessCmdLine("ls -foo -bar", shell=True)
    cmd4 = SubprocessCmdLine("ls -foo -bar", shell=True)
    cmd5 = SubprocessCmdLine("ls -foo -baz", shell=False)
    assert id(cmd1._cache_entry) == id(cmd2._cache_entry)
    assert id(cmd1._cache_entry) != id(cmd3._cache_entry)
    assert id(cmd3._cache_entry) == id(cmd4._cache_entry)
    assert id(cmd1._cache_entry) != id(cmd5._cache_entry)


def test_cache_maxsize():
    orig_cache_maxsize = SubprocessCmdLine._CACHE_MAXSIZE
    try:
        assert len(SubprocessCmdLine._CACHE) == 0
        SubprocessCmdLine._CACHE_MAXSIZE = 2
        cmd1 = SubprocessCmdLine("ls -foo -bar")  # first entry
        cmd1_catched = SubprocessCmdLine("ls -foo -bar")  # first entry
        assert len(SubprocessCmdLine._CACHE) == 1
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 1
        assert id(cmd1._cache_entry) == id(cmd1_catched._cache_entry)

        SubprocessCmdLine("other -foo -bar")  # second entry
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2

        SubprocessCmdLine("another -bar -foo")  # third entry, should remove first
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2

        cmd1_new = SubprocessCmdLine("ls -foo -bar")  # fourth entry since first was removed, removed second
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2
        assert id(cmd1._cache_entry) != id(cmd1_new._cache_entry)
    finally:
        SubprocessCmdLine._CACHE_MAXSIZE = orig_cache_maxsize
