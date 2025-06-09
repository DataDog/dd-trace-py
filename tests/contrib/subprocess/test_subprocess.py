import os
import subprocess
import sys

import pytest

from ddtrace.contrib.internal.subprocess.constants import COMMANDS
from ddtrace.contrib.internal.subprocess.patch import SubprocessCmdLine
from ddtrace.contrib.internal.subprocess.patch import patch
from ddtrace.contrib.internal.subprocess.patch import unpatch
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.trace import Pin
from tests.utils import override_config
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


allowed_envvars_fixture_list = []
for allowed in SubprocessCmdLine.ENV_VARS_ALLOWLIST:
    allowed_envvars_fixture_list.extend(
        [
            (
                SubprocessCmdLine(["%s=bar" % allowed, "BAR=baz", "dir", "-li", "/", "OTHER=any"], shell=True),
                ["%s=bar" % allowed, "BAR=?", "dir", "-li", "/", "OTHER=any"],
                ["%s=bar" % allowed, "BAR=?"],
                "dir",
                ["-li", "/", "OTHER=any"],
            ),
            (
                SubprocessCmdLine(
                    ["FOO=bar", "%s=bar" % allowed, "BAR=baz", "dir", "-li", "/", "OTHER=any"], shell=True
                ),
                ["FOO=?", "%s=bar" % allowed, "BAR=?", "dir", "-li", "/", "OTHER=any"],
                ["FOO=?", "%s=bar" % allowed, "BAR=?"],
                "dir",
                ["-li", "/", "OTHER=any"],
            ),
        ]
    )


@pytest.mark.parametrize(
    "cmdline_obj,full_list,env_vars,binary,arguments",
    [
        (
            SubprocessCmdLine(["FOO=bar", "BAR=baz", "dir", "-li", "/"], shell=True),
            ["FOO=?", "BAR=?", "dir", "-li", "/"],
            ["FOO=?", "BAR=?"],
            "dir",
            ["-li", "/"],
        ),
        (
            SubprocessCmdLine(["FOO=bar", "BAR=baz", "dir", "-li", "/dir with spaces", "OTHER=any"], shell=True),
            ["FOO=?", "BAR=?", "dir", "-li", "/dir with spaces", "OTHER=any"],
            ["FOO=?", "BAR=?"],
            "dir",
            ["-li", "/dir with spaces", "OTHER=any"],
        ),
        (
            SubprocessCmdLine(["FOO=bar", "lower=baz", "dir", "-li", "/", "OTHER=any"], shell=True),
            ["FOO=?", "lower=baz", "dir", "-li", "/", "OTHER=any"],
            ["FOO=?"],
            "lower=baz",
            ["dir", "-li", "/", "OTHER=any"],
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
                SubprocessCmdLine([denied, "-foo", "bar", "baz"]),
                [denied, "?", "?", "?"],
                ["?", "?", "?"],
            )
        ]
    )


@pytest.mark.parametrize(
    "cmdline_obj,full_list,arguments",
    [(SubprocessCmdLine(["dir", "-li", "/"]), ["dir", "-li", "/"], ["-li", "/"])],
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
                shell=False,
            ),
            ["-a", "?", "-passwd", "?", "-d bar", "?", "-efoo", "?", "?"],
        ),
    ],
)
def test_argument_scrubing(cmdline_obj, arguments):
    assert cmdline_obj.arguments == arguments


def test_argument_scrubing_user():
    with override_config("subprocess", dict(sensitive_wildcards=["*custom_scrub*", "*myscrub*"])):
        cmdline_obj = SubprocessCmdLine(
            [
                "binary",
                "-passwd",
                "SCRUB",
                "-d bar",
                "--custom_scrubed",
                "SCRUB",
                "--foomyscrub",
                "SCRUB",
            ],
            shell=False,
        )
        assert cmdline_obj.arguments == ["-passwd", "?", "-d bar", "--custom_scrubed", "?", "--foomyscrub", "?"]


@pytest.mark.parametrize(
    "cmdline_obj,expected_str,expected_list,truncated",
    [
        (
            SubprocessCmdLine(["dir", "-A" + "loremipsum" * 40, "-B"]),
            'dir -Aloremipsumloremipsumloremipsuml "4kB argument truncated by 329 characters"',
            ["dir", "-Aloremipsumloremipsumloremipsuml", "4kB argument truncated by 329 characters"],
            True,
        ),
        (SubprocessCmdLine("dir -A -B -C"), "dir -A -B -C", ["dir", "-A", "-B", "-C"], False),
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
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(os)._clone(tracer=tracer).onto(os)
        with tracer.trace("ossystem_test"):
            ret = os.system("dir -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "os"


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
def test_fork(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(os)._clone(tracer=tracer).onto(os)
        with tracer.trace("ossystem_test"):
            pid = os.fork()
            if pid == 0:
                # Exit, otherwise the rest of this process will continue to be pytest
                from ddtrace.contrib.internal.coverage.patch import unpatch

                unpatch()
                import pytest

                pytest.exit("in forked child", returncode=0)

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "fork"
        assert span.get_tag(COMMANDS.COMPONENT) == "os"
        assert span.get_tag(COMMANDS.EXEC) == str(["os.fork"])
        assert not span.get_tag(COMMANDS.TRUNCATED)


def test_unpatch(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(os)._clone(tracer=tracer).onto(os)
        with tracer.trace("os.system"):
            ret = os.system("dir -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[1]
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"

    unpatch()
    with override_global_config(dict(_ep_enabled=False)):
        Pin.get_from(os)._clone(tracer=tracer).onto(os)
        with tracer.trace("os.system_unpatch"):
            ret = os.system("dir -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) == 1
        span = spans[0]
        assert not span.get_tag(COMMANDS.EXEC)
        assert not span.get_tag(COMMANDS.SHELL)
        assert not span.get_tag(COMMANDS.EXIT_CODE)
        assert not span.get_tag(COMMANDS.COMPONENT)

    assert not getattr(os, "_datadogpatch", False)
    assert not getattr(subprocess, "_datadogpatch", False)


def test_ossystem_noappsec(tracer):
    with override_global_config(dict(_ep_enabled=False)):
        patch()
        assert not hasattr(os.system, "__wrapped__")
        assert not hasattr(os._spawnvef, "__wrapped__")
        assert not hasattr(subprocess.Popen.__init__, "__wrapped__")


def test_ospopen(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("os.popen"):
            pipe = os.popen("dir -li /")
            content = pipe.read()
            assert content
            pipe.close()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert span.get_tag(COMMANDS.SHELL) == "dir -li /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"


_PARAMS = ["/bin/ls", "-l", "/"]
_PARAMS_ENV = _PARAMS + [{"fooenv": "bar"}]  # type: ignore


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
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
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(os)._clone(tracer=tracer).onto(os)

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
        assert len(spans) == 3
        assert spans[0].get_tag(COMMANDS.EXEC) is None
        assert spans[0].get_tag(COMMANDS.COMPONENT) is None

        assert spans[2].get_tag(COMMANDS.EXEC) == "['os.fork']"
        assert spans[2].get_tag(COMMANDS.COMPONENT) == 'os'

        span = spans[1]
        if mode == os.P_WAIT:
            assert span.get_tag(COMMANDS.EXIT_CODE) == str(ret)

        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == arguments[0]
        param_arguments = arguments[1:-1] if "e" in cleaned_name else arguments[1:]
        assert span.get_tag(COMMANDS.EXEC) == str([arguments[0]] + param_arguments)
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "os"


def test_subprocess_init_shell_true(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["dir", "-li", "/"], shell=True)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert not span.get_tag(COMMANDS.EXEC)
        assert span.get_tag(COMMANDS.SHELL) == "dir -li /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"


def test_subprocess_init_shell_false(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["dir", "-li", "/"], shell=False)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert not span.get_tag(COMMANDS.SHELL)
        assert span.get_tag(COMMANDS.EXEC) == "['dir', '-li', '/']"


def test_subprocess_wait_shell_false(tracer):
    args = ["dir", "-li", "/"]
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(args=args, shell=False)
            subp.wait()

            assert not core.get_item(COMMANDS.CTX_SUBP_IS_SHELL)
            assert not core.get_item(COMMANDS.CTX_SUBP_TRUNCATED)
            assert core.get_item(COMMANDS.CTX_SUBP_LINE) == args


def test_subprocess_wait_shell_true(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(args=["dir", "-li", "/"], shell=True)
            subp.wait()

            assert core.get_item(COMMANDS.CTX_SUBP_IS_SHELL)


def test_subprocess_run(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.wait"):
            result = subprocess.run(["dir", "-l", "/"], shell=True)
            assert result.returncode == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert not span.get_tag(COMMANDS.EXEC)
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"


def test_subprocess_run_error(tracer):
    patch()
    with pytest.raises(FileNotFoundError):
        _ = subprocess.run(["fake"], stderr=subprocess.DEVNULL)


def test_subprocess_communicate(tracer):
    with override_global_config(dict(_asm_enabled=True)):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.wait"):
            subp = subprocess.Popen(args=["dir", "-li", "/"], shell=True)
            subp.communicate()
            subp.wait()
            assert subp.returncode == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) > 1
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert not span.get_tag(COMMANDS.EXEC)
        assert span.get_tag(COMMANDS.SHELL) == "dir -li /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"


def test_cache_hit():
    cmd1 = SubprocessCmdLine("dir -foo -bar", shell=False)
    cmd2 = SubprocessCmdLine("dir -foo -bar", shell=False)
    cmd3 = SubprocessCmdLine("dir -foo -bar", shell=True)
    cmd4 = SubprocessCmdLine("dir -foo -bar", shell=True)
    cmd5 = SubprocessCmdLine("dir -foo -baz", shell=False)
    assert id(cmd1._cache_entry) == id(cmd2._cache_entry)
    assert id(cmd1._cache_entry) != id(cmd3._cache_entry)
    assert id(cmd3._cache_entry) == id(cmd4._cache_entry)
    assert id(cmd1._cache_entry) != id(cmd5._cache_entry)


def test_cache_maxsize():
    orig_cache_maxsize = SubprocessCmdLine._CACHE_MAXSIZE
    try:
        assert len(SubprocessCmdLine._CACHE) == 0
        SubprocessCmdLine._CACHE_MAXSIZE = 2
        cmd1 = SubprocessCmdLine("dir -foo -bar")  # first entry
        cmd1_catched = SubprocessCmdLine("dir -foo -bar")  # first entry
        assert len(SubprocessCmdLine._CACHE) == 1
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 1
        assert id(cmd1._cache_entry) == id(cmd1_catched._cache_entry)

        SubprocessCmdLine("other -foo -bar")  # second entry
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2

        SubprocessCmdLine("another -bar -foo")  # third entry, should remove first
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2

        cmd1_new = SubprocessCmdLine("dir -foo -bar")  # fourth entry since first was removed, removed second
        assert len(SubprocessCmdLine._CACHE) == 2
        assert len(SubprocessCmdLine._CACHE_DEQUE) == 2
        assert id(cmd1._cache_entry) != id(cmd1_new._cache_entry)
    finally:
        SubprocessCmdLine._CACHE_MAXSIZE = orig_cache_maxsize
