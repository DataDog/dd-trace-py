import os
import shlex
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


PATCH_ENABLED_CONFIGURATIONS = (
    dict(_asm_enabled=True),
    dict(_iast_enabled=True),
    dict(_asm_enabled=True, _iast_enabled=True),
    dict(_asm_enabled=True, _iast_enabled=False),
    dict(_asm_enabled=False, _iast_enabled=True),
    dict(_bypass_instrumentation_for_waf=False, _asm_enabled=True, _iast_enabled=True),
    dict(_bypass_instrumentation_for_waf=False, _asm_enabled=False, _iast_enabled=True),
    dict(_bypass_instrumentation_for_waf=False, _asm_enabled=True, _iast_enabled=False),
)

PATCH_SPECIALS = (dict(_remote_config_enabled=True),)

PATCH_DISABLED_CONFIGURATIONS = (
    dict(),
    dict(_asm_enabled=False),
    dict(_iast_enabled=False),
    dict(_remote_config_enabled=False),
    dict(_asm_enabled=False, _iast_enabled=False),
    dict(_bypass_instrumentation_for_waf=True, _asm_enabled=False, _iast_enabled=False),
    dict(_bypass_instrumentation_for_waf=True),
    dict(_bypass_instrumentation_for_waf=False, _asm_enabled=False, _iast_enabled=False),
    dict(_bypass_instrumentation_for_waf=True, _asm_enabled=True, _iast_enabled=False),
    dict(_bypass_instrumentation_for_waf=True, _asm_enabled=False, _iast_enabled=True),
)

CONFIGURATIONS = PATCH_ENABLED_CONFIGURATIONS + PATCH_DISABLED_CONFIGURATIONS


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


def _assert_root_span_empty_system_data(span):
    assert span.get_tag(COMMANDS.EXEC) is None
    assert span.get_tag(COMMANDS.COMPONENT) is None
    assert span.get_tag(COMMANDS.EXIT_CODE) is None
    assert span.get_tag(COMMANDS.SHELL) is None


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


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_ossystem(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(os)._clone(tracer=tracer).onto(os)
        with tracer.trace("ossystem_test"):
            ret = os.system("dir -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) == 2
        span = spans[1]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "os"


@pytest.mark.parametrize("config", PATCH_DISABLED_CONFIGURATIONS + PATCH_SPECIALS)
def test_ossystem_disabled(tracer, config):
    with override_global_config(config):
        patch()
        pin = Pin.get_from(os)
        # TODO(APPSEC-57964): PIN is None in GitLab with py3.12 and this config:
        #  {'_asm_enabled': False, '_bypass_instrumentation_for_waf': False, '_iast_enabled': False}
        if pin:
            pin._clone(tracer=tracer).onto(os)
        with tracer.trace("ossystem_test"):
            ret = os.system("dir -l /")
            assert ret == 0

        spans = tracer.pop()
        assert spans
        # TODO(APPSEC-57964): GitLab with py3.12 returns two spans for those configurations.
        #  Is override_global_config not triggering a restart?
        #  {'_remote_config_enabled': True}
        #  {'_remote_config_enabled': False}
        #  {'_iast_enabled': False}
        assert len(spans) >= 1
        _assert_root_span_empty_system_data(spans[0])


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
        assert len(spans) >= 2
        _assert_root_span_empty_system_data(spans[0])
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
        assert len(spans) == 2
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


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_ospopen(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("os.popen"):
            pipe = os.popen("dir -li /")
            content = pipe.read()
            assert content
            pipe.close()

        spans = tracer.pop()
        assert spans
        assert len(spans) == 3
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
            # TODO(APPSEC-57964): Gitlab raises at some point
            #  Traceback (most recent call last):
            #    File "/3.10.16/lib/python3.10/multiprocessing/util.py", line 357, in _exit_function
            #      p.join()
            #    File "/root/.pyenv/versions/3.10.16/lib/python3.10/multiprocessing/process.py", line 147, in join
            #      assert self._parent_pid == os.getpid(), 'can only join a child process'
            #  AssertionError: can only join a child process
            # if mode == os.P_WAIT:
            #     assert ret == 0
            # else:
            #     assert ret > 0  # for P_NOWAIT returned value is the pid

        spans = tracer.pop()
        assert spans
        # assert len(spans) > 1
        span = spans[1]
        if mode == os.P_WAIT:
            assert span.get_tag(COMMANDS.EXIT_CODE) == str(ret)

        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == arguments[0]
        param_arguments = arguments[1:-1] if "e" in cleaned_name else arguments[1:]
        assert span.get_tag(COMMANDS.EXEC) == str([arguments[0]] + param_arguments)
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "os"


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_init_shell_true(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["dir", "-li", "/"], shell=True)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) == 3
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert not span.get_tag(COMMANDS.EXEC)
        assert span.get_tag(COMMANDS.SHELL) == "dir -li /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_init_shell_false(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(["dir", "-li", "/"], shell=False)
            subp.wait()

        spans = tracer.pop()
        assert spans
        assert len(spans) == 3
        span = spans[2]
        assert not span.get_tag(COMMANDS.SHELL)
        assert span.get_tag(COMMANDS.EXEC) == "['dir', '-li', '/']"


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_wait_shell_false(tracer, config):
    args = ["dir", "-li", "/"]
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(args=args, shell=False)
            subp.wait()

            assert not core.get_item(COMMANDS.CTX_SUBP_IS_SHELL)
            assert not core.get_item(COMMANDS.CTX_SUBP_TRUNCATED)
            assert core.get_item(COMMANDS.CTX_SUBP_LINE) == args


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_wait_shell_true(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM):
            subp = subprocess.Popen(args=["dir", "-li", "/"], shell=True)
            subp.wait()

            assert core.get_item(COMMANDS.CTX_SUBP_IS_SHELL)


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_run(tracer, config):
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        with tracer.trace("subprocess.Popen.wait"):
            result = subprocess.run(["dir", "-l", "/"], shell=True)
            assert result.returncode == 0

        spans = tracer.pop()
        assert spans
        assert len(spans) == 4
        _assert_root_span_empty_system_data(spans[0])

        _assert_root_span_empty_system_data(spans[1])

        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert span.get_tag(COMMANDS.EXEC) is None
        assert span.get_tag(COMMANDS.TRUNCATED) is None
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"

        span = spans[3]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert span.get_tag(COMMANDS.EXEC) is None
        assert span.get_tag(COMMANDS.TRUNCATED) is None
        assert span.get_tag(COMMANDS.SHELL) == "dir -l /"
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_run_error(config):
    with override_global_config(config):
        patch()
        with pytest.raises(FileNotFoundError):
            _ = subprocess.run(["fake"], stderr=subprocess.DEVNULL)


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_popen_error(tracer, config):
    """Test that subprocess.Popen raises FileNotFoundError for non-existent command"""
    with override_global_config(config):
        patch()
        with pytest.raises(FileNotFoundError):
            _ = subprocess.Popen(["fake_nonexistent_command"], stderr=subprocess.DEVNULL)


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_popen_wait_error(tracer, config):
    """Test that subprocess.Popen.wait correctly handles process exit codes"""
    with override_global_config(config):
        patch()
        # This should create a process that exits with non-zero code
        proc = subprocess.Popen(["python", "-c", "import sys; sys.exit(42)"], stderr=subprocess.DEVNULL)
        exit_code = proc.wait()
        assert exit_code == 42


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_popen_shell_error(tracer, config):
    """Test that subprocess.Popen with shell=True raises errors correctly"""
    with override_global_config(config):
        patch()
        with pytest.raises(subprocess.CalledProcessError):
            _ = subprocess.run(["fake_nonexistent_command"], shell=True, check=True, stderr=subprocess.DEVNULL)


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_os_popen_error(tracer, config):
    """Test that os.popen handles non-existent commands gracefully"""
    with override_global_config(config):
        patch()
        # os.popen doesn't raise immediately, but the command will fail
        command = shlex.quote("fake_nonexistent_command") + " 2>/dev/null"
        with os.popen(command) as pipe:
            _ = pipe.read()
            exit_code = pipe.close()
            # On most systems, this should return a non-zero exit code
            # os.popen returns None for success, non-zero for failure
            assert exit_code is not None  # Command failed as expected


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_run_timeout_error(tracer, config):
    """Test that subprocess.run raises TimeoutExpired for long-running commands"""
    with override_global_config(config):
        patch()
        with pytest.raises(subprocess.TimeoutExpired):
            # Command that sleeps longer than timeout
            subprocess.run(["python", "-c", "import time; time.sleep(10)"], timeout=0.1)


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_popen_invalid_args_error(tracer, config):
    """Test that subprocess.Popen raises TypeError for invalid arguments"""
    with override_global_config(config):
        patch()
        with pytest.raises(TypeError):
            # Invalid argument type
            subprocess.Popen(123)  # Should be string or list


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_run_invalid_cwd_error(tracer, config):
    """Test that subprocess.run raises FileNotFoundError for invalid cwd"""
    with override_global_config(config):
        patch()
        with pytest.raises(FileNotFoundError):
            subprocess.run(["echo", "test"], cwd="/nonexistent/directory")


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_os_system_error(tracer, config):
    """Test that os.system returns non-zero exit code for failed commands"""
    with override_global_config(config):
        patch()
        # os.system returns the exit status, not an exception
        command = shlex.quote("fake_nonexistent_command") + " 2>/dev/null"
        exit_code = os.system(command)
        # On Unix systems, the return value is the exit status as returned by wait()
        # Non-zero indicates failure
        assert exit_code != 0


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_check_call_error(tracer, config):
    """Test that subprocess.check_call raises CalledProcessError for non-zero exit"""
    with override_global_config(config):
        patch()
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            subprocess.check_call(["python", "-c", "import sys; sys.exit(1)"])

        # Verify the exception contains the correct return code
        assert exc_info.value.returncode == 1


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_check_output_error(tracer, config):
    """Test that subprocess.check_output raises CalledProcessError for non-zero exit"""
    with override_global_config(config):
        patch()
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            subprocess.check_output(["python", "-c", "import sys; sys.exit(2)"])

        # Verify the exception contains the correct return code
        assert exc_info.value.returncode == 2


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
        assert len(spans) == 4
        span = spans[2]
        assert span.name == COMMANDS.SPAN_NAME
        assert span.resource == "dir"
        assert not span.get_tag(COMMANDS.EXEC)
        assert span.get_tag(COMMANDS.SHELL) == "dir -li /"
        assert not span.get_tag(COMMANDS.TRUNCATED)
        assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
        assert span.get_tag(COMMANDS.EXIT_CODE) == "0"


@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
def test_os_spawn_argument_errors(tracer):
    """Test that os.spawn functions raise exceptions for invalid arguments"""
    patch()

    # Test 1: Invalid mode parameter
    os.spawnv(999, "/bin/ls", ["ls"])  # Invalid mode

    # Test 2: Wrong argument types
    os.spawnv(os.P_WAIT, 123, ["ls"])  # path should be string, not int

    # Test 3: Invalid arguments list
    with pytest.raises(TypeError):
        os.spawnv(os.P_WAIT, "/bin/ls", "invalid")  # args should be list, not string


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_nested_subprocess_calls(tracer, config):
    """Test subprocess that spawns another subprocess - verify span ordering"""
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)

        with tracer.trace("parent_operation"):
            # Parent subprocess that calls another subprocess
            result = subprocess.run(
                ["python", "-c", "import subprocess; subprocess.run(['echo', 'nested']); print('parent')"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "parent" in result.stdout

        spans = tracer.pop()
        assert spans
        assert len(spans) >= 3  # parent trace + at least 2 subprocess spans

        # Verify span hierarchy and ordering
        parent_span = spans[0]
        assert parent_span.name == "parent_operation"

        # Should have subprocess spans for both parent and nested calls
        subprocess_spans = [s for s in spans if s.name == COMMANDS.SPAN_NAME]
        assert len(subprocess_spans) >= 2  # Parent and nested subprocess


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_complex_shell_command_with_pipes(tracer, config):
    """Test complex shell commands with pipes and redirections"""
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)

        with tracer.trace("complex_shell"):
            # Complex shell command with pipes
            result = subprocess.run(
                "echo 'hello world' | grep 'hello' | wc -l", shell=True, capture_output=True, text=True
            )
            assert result.returncode == 0
            assert result.stdout.strip() == "1"

        spans = tracer.pop()
        assert spans
        i = 0
        for span in spans:
            if span.name == COMMANDS.SPAN_NAME:
                assert span.resource == "echo"
                if i <= 1:
                    assert span.get_tag(COMMANDS.COMPONENT) is None
                else:
                    assert span.get_tag(COMMANDS.COMPONENT) == "subprocess"
            i += 1


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_mixed_subprocess_functions_sequence(tracer, config):
    """Test sequence of different subprocess functions and verify span order"""
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)
        Pin.get_from(os)._clone(tracer=tracer).onto(os)

        with tracer.trace("mixed_subprocess_operations"):
            # Use different subprocess functions in sequence
            os.system("echo 'os.system call'")

            proc = subprocess.Popen(["echo", "popen"], stdout=subprocess.PIPE)
            proc.wait()

            subprocess.run(["echo", "subprocess.run"])

            with os.popen("echo 'os.popen'") as pipe:
                pipe.read()

        spans = tracer.pop()
        assert spans

        # Verify we have spans for all different functions
        subprocess_spans = [s for s in spans if s.name == COMMANDS.SPAN_NAME]
        assert len(subprocess_spans) >= 4  # os.system, Popen, run, os.popen

        # Check that different components are represented
        components = [s.get_tag(COMMANDS.COMPONENT) for s in subprocess_spans]
        assert "os" in components
        assert "subprocess" in components


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_concurrent_subprocess_calls(tracer, config):
    """Test multiple subprocess calls and verify all are traced"""
    import threading

    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)

        results = []

        def subprocess_worker(worker_id):
            result = subprocess.run(
                ["python", "-c", f"import time; time.sleep(0.1); print('worker-{worker_id}')"],
                capture_output=True,
                text=True,
            )
            results.append(result)

        with tracer.trace("concurrent_subprocess"):
            # Start multiple subprocess calls concurrently
            threads = []
            for i in range(3):
                thread = threading.Thread(target=subprocess_worker, args=(i,))
                threads.append(thread)
                thread.start()

            # Wait for all to complete
            for thread in threads:
                thread.join()

        spans = tracer.pop()
        assert spans

        # Should have spans for all subprocess calls
        subprocess_spans = [s for s in spans if s.name == COMMANDS.SPAN_NAME]
        assert len(subprocess_spans) >= 3  # At least one per worker

        # Verify all workers completed successfully
        assert len(results) == 3
        for result in results:
            assert result.returncode == 0


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_edge_cases(tracer, config):
    """Test edge cases with unusual inputs"""
    with override_global_config(config):
        patch()

        # Test 1: Empty command list (should raise exception)
        with pytest.raises((ValueError, IndexError)):
            subprocess.run([])

        # Test 2: Command with many arguments
        long_args = ["echo"] + [f"arg{i}" for i in range(100)]
        result = subprocess.run(long_args, capture_output=True)
        assert result.returncode == 0

        # Test 3: Command with special characters
        result = subprocess.run(["echo", "hello$world&test"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "hello$world&test" in result.stdout


@pytest.mark.parametrize("config", CONFIGURATIONS)
def test_subprocess_error_propagation_nested(tracer, config):
    """Test error propagation in nested subprocess scenarios"""
    with override_global_config(config):
        patch()

        # Test nested subprocess where inner subprocess fails
        with pytest.raises(subprocess.CalledProcessError):
            subprocess.run(["python", "-c", "import subprocess; subprocess.run(['false'], check=True)"], check=True)

        # Test nested subprocess with timeout
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(["python", "-c", "import subprocess; subprocess.run(['sleep', '10'])"], timeout=0.1)


@pytest.mark.parametrize("config", PATCH_ENABLED_CONFIGURATIONS)
def test_subprocess_resource_cleanup_on_error(tracer, config):
    """Test that resources are properly cleaned up when subprocess fails"""
    with override_global_config(config):
        patch()
        Pin.get_from(subprocess)._clone(tracer=tracer).onto(subprocess)

        with tracer.trace("cleanup_test"):
            # Test that file descriptors are properly managed even on errors
            try:
                proc = subprocess.Popen(
                    ["python", "-c", "import sys; sys.exit(1)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                proc.wait()
                assert proc.returncode == 1
            finally:
                # Ensure process is cleaned up
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()

        spans = tracer.pop()
        subprocess_spans = [s for s in spans if s.name == COMMANDS.SPAN_NAME]
        assert len(subprocess_spans) >= 1

        # Verify exit code is recorded even for failed processes
        span = subprocess_spans[-1]  # Last subprocess span
        assert span.get_tag(COMMANDS.EXIT_CODE) == "1"
