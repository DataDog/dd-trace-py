import os
import subprocess
import sys
import tempfile

import pytest

import ddtrace

from ..utils import BaseTestCase
from ..utils import override_env


def inject_sitecustomize(path):
    """Creates a new environment, injecting a ``sitecustomize.py`` module in
    the current PYTHONPATH.

    :param path: package path containing ``sitecustomize.py`` module, starting
                 from the ddtrace root folder
    :returns: a cloned environment that includes an altered PYTHONPATH with
              the given `sitecustomize.py`
    """
    from ddtrace import __file__ as root_file

    root_folder = os.path.dirname(root_file)
    # Copy the current environment and replace the PYTHONPATH. This is
    # required otherwise `ddtrace` scripts are not found when `env` kwarg is
    # passed
    env = os.environ.copy()
    sitecustomize = os.path.abspath(os.path.join(root_folder, "..", path))

    # Add `bootstrap` directory to the beginning of PYTHONTPATH so we know
    # if `import sitecustomize` is run that it'll be the one we specify
    python_path = [sitecustomize] + list(sys.path)
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    return env


class DdtraceRunTest(BaseTestCase):
    def test_env_enabling(self):
        """
        DD_TRACE_ENABLED=false allows disabling of the global tracer
        """

        with self.override_env(dict(DD_TRACE_ENABLED="false")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_disabled.py"])
            assert out.startswith(b"Test success")

        with self.override_env(dict(DD_TRACE_ENABLED="true")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_enabled.py"])
            assert out.startswith(b"Test success")

    def test_patched_modules(self):
        """
        Using `ddtrace-run` registers some generic patched modules
        """
        out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_patched_modules.py"])
        assert out.startswith(b"Test success")

    def test_integration(self):
        out = subprocess.check_output(["ddtrace-run", "python", "-m", "tests.commands.ddtrace_run_integration"])
        assert out.startswith(b"Test success")

    def test_debug_enabling(self):
        """
        DD_TRACE_DEBUG=true allows setting debug logging of the global tracer.
        """
        with self.override_env(dict(DD_TRACE_DEBUG="false")):
            out = subprocess.check_output(
                ["ddtrace-run", "python", "tests/commands/ddtrace_run_no_debug.py"], stderr=subprocess.STDOUT
            )
            assert b"Test success" in out
            assert b"DATADOG TRACER CONFIGURATION" not in out

        with self.override_env(dict(DD_TRACE_DEBUG="true")):
            out = subprocess.check_output(
                ["ddtrace-run", "python", "tests/commands/ddtrace_run_debug.py"],
                stderr=subprocess.STDOUT,
            )
            assert b"Test success" in out
            assert b"DATADOG TRACER CONFIGURATION" in out

    def test_host_port_from_env(self):
        """
        DD_TRACE_AGENT_HOSTNAME|PORT point the tracer to the correct host/port for submission
        """

        with self.override_env(dict(DD_TRACE_AGENT_HOSTNAME="172.10.0.1", DD_TRACE_AGENT_PORT="8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_hostname.py"])
            assert out.startswith(b"Test success")

    def test_host_port_from_env_dd(self):
        """
        DD_AGENT_HOST|DD_TRACE_AGENT_PORT point to the tracer
        to the correct host/port for submission
        """
        with self.override_env(dict(DD_AGENT_HOST="172.10.0.1", DD_TRACE_AGENT_PORT="8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_hostname.py"])
            assert out.startswith(b"Test success")

            # Do we get the same results without `ddtrace-run`?
            out = subprocess.check_output(["python", "tests/commands/ddtrace_run_hostname.py"])
            assert out.startswith(b"Test success")

    def test_dogstatsd_client_env_host_and_port(self):
        """
        DD_AGENT_HOST and DD_DOGSTATSD_PORT used to configure dogstatsd with udp in tracer
        """
        with self.override_env(dict(DD_AGENT_HOST="172.10.0.1", DD_DOGSTATSD_PORT="8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_dogstatsd.py"])
            assert out.startswith(b"Test success")

    def test_dogstatsd_client_env_url_host_and_port(self):
        """
        DD_DOGSTATSD_URL=<host>:<port> used to configure dogstatsd with udp in tracer
        """
        with self.override_env(dict(DD_DOGSTATSD_URL="172.10.0.1:8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_dogstatsd.py"])
            assert out.startswith(b"Test success")

    def test_dogstatsd_client_env_url_udp(self):
        """
        DD_DOGSTATSD_URL=udp://<host>:<port> used to configure dogstatsd with udp in tracer
        """
        with self.override_env(dict(DD_DOGSTATSD_URL="udp://172.10.0.1:8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_dogstatsd.py"])
            assert out.startswith(b"Test success")

    def test_dogstatsd_client_env_url_unix(self):
        """
        DD_DOGSTATSD_URL=unix://<path> used to configure dogstatsd with socket path in tracer
        """
        with self.override_env(dict(DD_DOGSTATSD_URL="unix:///dogstatsd.sock")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_dogstatsd.py"])
            assert out.startswith(b"Test success")

    def test_dogstatsd_client_env_url_path(self):
        """
        DD_DOGSTATSD_URL=<path> used to configure dogstatsd with socket path in tracer
        """
        with self.override_env(dict(DD_DOGSTATSD_URL="/dogstatsd.sock")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_dogstatsd.py"])
            assert out.startswith(b"Test success")

    def test_patch_modules_from_env(self):
        """
        DD_PATCH_MODULES overrides the defaults for _patch_all()
        """
        with self.override_env(
            env=dict(
                DD_PATCH_MODULES="flask:true,gevent:true,django:false,boto:true,falcon:false",
                DD_TRACE_FLASK_ENABLED="false",
                DD_TRACE_GEVENT_ENABLED="false",
                DD_TRACE_FALCON_ENABLED="true",
            )
        ):
            out = subprocess.check_output(
                ["ddtrace-run", "python", "tests/commands/ddtrace_run_patched_modules_overrides.py"],
            )
            assert out.startswith(b"Test success"), out

    def test_sitecustomize_without_ddtrace_run_command(self):
        # [Regression test]: ensure `sitecustomize` path is removed only if it's
        # present otherwise it will cause:
        #   ValueError: list.remove(x): x not in list
        # as mentioned here: https://github.com/DataDog/dd-trace-py/pull/516
        env = inject_sitecustomize("")
        out = subprocess.check_output(
            ["python", "tests/commands/ddtrace_minimal.py"],
            env=env,
        )
        # `out` contains the `loaded` status of the module
        result = out[:-1] == b"True"
        self.assertTrue(result)

    def test_sitecustomize_run(self):
        # [Regression test]: ensure users `sitecustomize.py` is properly loaded,
        # so that our `bootstrap/sitecustomize.py` doesn't override the one
        # defined in users' PYTHONPATH.
        env = inject_sitecustomize("tests/commands/bootstrap")
        out = subprocess.check_output(
            ["ddtrace-run", "python", "tests/commands/ddtrace_run_sitecustomize.py"],
            env=env,
        )
        assert out.startswith(b"Test success")

    def test_sitecustomize_run_suppressed(self):
        # ensure `sitecustomize.py` is not loaded if `-S` is used
        env = inject_sitecustomize("tests/commands/bootstrap")
        out = subprocess.check_output(
            ["ddtrace-run", "python", "-S", "tests/commands/ddtrace_run_sitecustomize.py", "-S"],
            env=env,
        )
        assert out.startswith(b"Test success")

    def test_argv_passed(self):
        out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_argv.py", "foo", "bar"])
        assert out.startswith(b"Test success")

    def test_got_app_name(self):
        """
        apps run with ddtrace-run have a proper app name
        """
        out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_app_name.py"])
        assert out.startswith(b"ddtrace_run_app_name.py")

    def test_global_trace_tags(self):
        """Ensure global tags are passed in from environment"""
        with self.override_env(dict(DD_TRACE_GLOBAL_TAGS="a:True,b:0,c:C")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_global_tags.py"])
            assert out.startswith(b"Test success")

    def test_logs_injection(self):
        """Ensure logs injection works"""
        with self.override_env(dict(DD_LOGS_INJECTION="true")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_logs_injection.py"])
            assert out.startswith(b"Test success"), out.decode()

    def test_debug_mode(self):
        p = subprocess.Popen(
            ["ddtrace-run", "--debug", "python", "-c", "''"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        p.wait()
        assert p.returncode == 0
        assert p.stdout.read() == b""
        assert b"debug mode has been enabled for the ddtrace logger" in p.stderr.read()


@pytest.mark.skipif(sys.version_info > (3, 12), reason="Profiling unsupported with 3.13")
def test_env_profiling_enabled(monkeypatch):
    """DD_PROFILING_ENABLED allows enabling the global profiler."""
    # Off by default
    out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_profiling.py"])
    assert out.strip() == b"NO PROFILER"

    monkeypatch.setenv("DD_PROFILING_ENABLED", "true")
    out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_profiling.py"])
    assert out.strip() == b"ServiceStatus.RUNNING"

    monkeypatch.setenv("DD_PROFILING_ENABLED", "false")
    out = subprocess.check_output(["ddtrace-run", "--profiling", "python", "tests/commands/ddtrace_run_profiling.py"])
    assert out.strip() == b"ServiceStatus.RUNNING"

    monkeypatch.setenv("DD_PROFILING_ENABLED", "false")
    out = subprocess.check_output(["ddtrace-run", "-p", "python", "tests/commands/ddtrace_run_profiling.py"])
    assert out.strip() == b"ServiceStatus.RUNNING"

    monkeypatch.setenv("DD_PROFILING_ENABLED", "false")
    out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_profiling.py"])
    assert out.strip() == b"NO PROFILER"


def test_version():
    p = subprocess.Popen(
        ["ddtrace-run", "-v"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 0

    assert p.stdout.read() == ("ddtrace-run %s\n" % ddtrace.__version__).encode("utf-8")

    p = subprocess.Popen(
        ["ddtrace-run", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 0
    assert p.stdout.read() == ("ddtrace-run %s\n" % ddtrace.__version__).encode("utf-8")


def test_bad_executable():
    p = subprocess.Popen(
        ["ddtrace-run", "executable-does-not-exist"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 1
    content = p.stdout.read()
    assert b"ddtrace-run: failed to bootstrap: args" in content
    assert b"'executable-does-not-exist'" in content


def test_executable_no_perms():
    fd, path = tempfile.mkstemp(suffix=".py")
    p = subprocess.Popen(
        ["ddtrace-run", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 1

    out = p.stdout.read()
    assert out.startswith(("ddtrace-run: permission error while launching '%s'" % path).encode("utf-8"))


def test_command_flags():
    out = subprocess.check_output(["ddtrace-run", "python", "-c", "print('test!')"])
    assert out.strip() == b"test!"


def test_return_code():
    p = subprocess.Popen(
        ["ddtrace-run", "python", "-c", "import sys;sys.exit(4)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    p.wait()

    assert p.returncode == 4


def test_info_no_configs():
    p = subprocess.Popen(
        ["ddtrace-run", "--info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    stdout = p.stdout.read()
    # checks most of the output but some pieces are removed due to the dynamic nature of the output
    expected_strings = [
        b"\x1b[1mTracer Configurations:\x1b[0m",
        b"Tracer enabled: True",
        b"Application Security enabled: False",
        b"Remote Configuration enabled: False",
        b"Debug logging: False",
        b"Log injection enabled: False",
        b"Health metrics enabled: False",
        b"Partial flushing enabled: True",
        b"Partial flush minimum number of spans: 300",
        b"WAF timeout: 5.0 msecs",
        b"Tagging:",
        b"DD Service: None",
        b"DD Env: None",
        b"DD Version: None",
        b"Global Tags: None",
        b"Tracer Tags: None",
        b"Summary",
        b"WARNING SERVICE NOT SET: It is recommended that a service tag be set for all traced applications.",
        b"For more information please see https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m",
        b"WARNING ENV NOT SET: It is recommended that an env tag be set for all traced applications. For more",
        b"information please see https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m",
        b"WARNING VERSION NOT SET: It is recommended that a version tag be set for all traced applications.",
        b"For more information please see https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m",
    ]
    for expected in expected_strings:
        assert expected in stdout, f"Expected string not found in output: {expected.decode()}"

    assert p.returncode == 0


def test_info_w_configs():
    with override_env(
        dict(
            DD_SERVICE="tester",
            DD_APPSEC_ENABLED="true",
            DD_REMOTE_CONFIGURATION_ENABLED="true",
            DD_IAST_ENABLED="true",
            DD_ENV="dev",
            DD_VERSION="0.45",
            DD_TRACE_DEBUG="true",
            DD_LOGS_INJECTION="true",
            DD_AGENT_HOST="168.212.226.204",
            DD_TRACE_PARTIAL_FLUSH_ENABLED="true",
            DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="1000",
        )
    ):
        p = subprocess.Popen(
            ["ddtrace-run", "--info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    p.wait()
    stdout = p.stdout.read()
    # checks most of the output but some pieces are removed due to the dynamic nature of the output
    expected_strings = [
        b"1mTracer Configurations:\x1b[0m",
        b"Tracer enabled: True",
        b"Remote Configuration enabled: True",
        b"IAST enabled (experimental)",
        b"Debug logging: True",
        b"Log injection enabled: True",
        b"Health metrics enabled: False",
        b"Partial flushing enabled: True",
        b"Partial flush minimum number of spans: 1000",
        b"WAF timeout: 5.0 msecs",
        b"Tagging:",
        b"DD Service: tester",
        b"DD Env: dev",
        b"DD Version: 0.45",
        b"Global Tags: None",
        b"Tracer Tags: None",
        b"m\x1b[1mSummary\x1b[0m",
    ]

    for expected in expected_strings:
        assert expected in stdout, f"Expected string not found in output: {expected.decode()}"

    assert p.returncode == 0


def test_no_args():
    p = subprocess.Popen(
        ["ddtrace-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 1
    assert b"usage:" in p.stdout.read()


MODULES_TO_CHECK = ["asyncio"]
MODULES_TO_CHECK_PARAMS = dict(
    DD_TRACE_ENABLED=["1", "0"],
    DD_PROFILING_ENABLED=["1", "0"],
    DD_DYNAMIC_INSTRUMENTATION_ENABLED=["1", "0"],
)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(MODULES_TO_CHECK=",".join(MODULES_TO_CHECK)),
    parametrize=MODULES_TO_CHECK_PARAMS,
    err=None,
)
def test_ddtrace_run_imports(MODULES_TO_CHECK_PARAMS):
    import os
    import sys

    MODULES_TO_CHECK = os.environ["MODULES_TO_CHECK"].split(",")

    for module in MODULES_TO_CHECK:
        assert module not in sys.modules, module


@pytest.mark.subprocess(
    env=dict(MODULES_TO_CHECK=",".join(MODULES_TO_CHECK)),
    parametrize=MODULES_TO_CHECK_PARAMS,
    err=None,
)
def test_ddtrace_auto_imports():
    import os
    import sys

    MODULES_TO_CHECK = os.environ["MODULES_TO_CHECK"].split(",")

    for module in MODULES_TO_CHECK:
        assert module not in sys.modules, module

    import ddtrace.auto  # noqa: F401

    for module in MODULES_TO_CHECK:
        assert module not in sys.modules, module


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="1"))
def test_ddtrace_re_module():
    import re

    re.Scanner(
        (
            ("frozen", None),
            (r"[a-zA-Z0-9_]+", lambda s, t: t),
            (r"[\s,<>]", None),
        )
    )


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_ddtrace_run_sitecustomize():
    """When using ddtrace-run we ensure ddtrace.bootstrap.sitecustomize is in sys.module cache"""
    import sys

    assert "ddtrace.bootstrap.sitecustomize" in sys.modules

    assert sys.modules["ddtrace.bootstrap.sitecustomize"].loaded


@pytest.mark.subprocess(ddtrace_run=False, err=None)
def test_ddtrace_auto_sitecustomize():
    """When import ddtrace.auto we ensure ddtrace.bootstrap.sitecustomize is in sys.module cache"""
    import sys

    import ddtrace.auto  # noqa: F401

    assert "ddtrace.bootstrap.sitecustomize" in sys.modules

    assert sys.modules["ddtrace.bootstrap.sitecustomize"].loaded


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_ddtrace_run_and_auto_sitecustomize():
    """When using ddtrace-run and import ddtrace.auto we don't double import sitecustomize"""
    import sys

    assert sys.modules["ddtrace.bootstrap.sitecustomize"].loaded

    # Capture the list of all loaded modules
    starting_modules = set(sys.modules.keys())

    assert "ddtrace.auto" not in starting_modules

    # Setting this to false, and confirming that importing auto doesn't set it to True (sitecustomize code ran)
    sys.modules["ddtrace.bootstrap.sitecustomize"].loaded = False

    import ddtrace.auto  # noqa: F401

    # Ensure we didn't re-load our sitecustomize module, which sets loaded = True
    assert sys.modules["ddtrace.bootstrap.sitecustomize"].loaded is False

    # Compare the list of imported modules before/after ddtrace.auto to show it is a no-op with
    # no additional modules imported / side-effects
    final_modules = set(sys.modules.keys())
    assert final_modules - starting_modules == set(["ddtrace.auto"])


@pytest.mark.subprocess(env=dict(DD_TRACE_GLOBAL_TAGS="a:True"), err=None)
def test_global_trace_tags_deprecation_warning():
    """Ensure DD_TRACE_GLOBAL_TAGS deprecation warning shows"""
    import warnings

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        import ddtrace.auto  # noqa: F401

        assert len(warns) == 1
        warning_message = str(warns[0].message)
        assert (
            warning_message
            == "DD_TRACE_GLOBAL_TAGS is deprecated and will be removed in version '4.0.0': Please migrate to using DD_TAGS instead"  # noqa: E501
        ), warning_message


@pytest.mark.subprocess(ddtrace_run=False, err="")
def test_ddtrace_auto_atexit():
    """When ddtrace-run is used, ensure atexit hooks are registered exactly once"""
    import sys

    from mock import patch

    registered_funcs = set()
    unregistered_funcs = set()

    def mock_register(func):
        if func in registered_funcs:
            raise AssertionError("Duplicate registered function: %s" % func)
        registered_funcs.add(func)

    def mock_unregister(func):
        if func in unregistered_funcs:
            raise AssertionError("Duplicate unregistered function: %s" % func)
        unregistered_funcs.add(func)

    with patch("ddtrace.internal.atexit.register", side_effect=mock_register), patch(
        "ddtrace.internal.atexit.unregister", side_effect=mock_unregister
    ), patch("ddtrace.internal.atexit.register_on_exit_signal", side_effect=mock_register), patch(
        "atexit.register", side_effect=mock_register
    ), patch(
        "atexit.unregister", side_effect=mock_unregister
    ):
        # Create and shutdown a tracer
        import ddtrace.auto  # noqa: F401
        from ddtrace.trace import tracer

        assert "ddtrace.bootstrap.sitecustomize" in sys.modules
        assert sys.modules["ddtrace.bootstrap.sitecustomize"].loaded
        tracer.shutdown()
        tracer.shutdown()
        tracer.shutdown()

    assert registered_funcs, "No registered functions"
    assert unregistered_funcs, "No unregistered functions"
