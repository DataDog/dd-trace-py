import os
import subprocess
import sys
import tempfile

import six

import ddtrace
from ddtrace.internal.compat import PY3

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
    def test_service_name_passthrough_legacy(self):
        """
        $DATADOG_SERVICE_NAME gets passed through to the program
        """
        with self.override_env(dict(DATADOG_SERVICE_NAME="my_test_service")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_service.py"])
            assert out.startswith(b"Test success")

    def test_service_name_passthrough(self):
        """
        $DD_SERVICE gets passed through to the program
        """
        with self.override_env(dict(DD_SERVICE="my_test_service")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_service.py"])
            assert out.startswith(b"Test success")

    def test_env_name_passthrough(self):
        """
        $DATADOG_ENV or $DD_ENV gets passed through to the global tracer as an 'env' tag
        """
        with self.override_env(dict(DATADOG_ENV="test")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_env.py"])
            assert out.startswith(b"Test success")

        with self.override_env(dict(DD_ENV="test")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_env.py"])
            assert out.startswith(b"Test success")

    def test_env_enabling(self):
        """
        DATADOG_TRACE_ENABLED=false and DD_TRACE_ENABLED=false allows disabling of the global tracer
        """
        with self.override_env(dict(DATADOG_TRACE_ENABLED="false")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_disabled.py"])
            assert out.startswith(b"Test success")

        with self.override_env(dict(DATADOG_TRACE_ENABLED="true")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_enabled.py"])
            assert out.startswith(b"Test success")

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
        Test DATADOG_TRACE_DEBUG flag for backwards compatibility
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

        with self.override_env(dict(DATADOG_TRACE_DEBUG="false")):
            out = subprocess.check_output(
                ["ddtrace-run", "python", "tests/commands/ddtrace_run_no_debug.py"], stderr=subprocess.STDOUT
            )
            assert b"Test success" in out
            assert b"DATADOG TRACER CONFIGURATION" not in out

        with self.override_env(dict(DATADOG_TRACE_DEBUG="true")):
            out = subprocess.check_output(
                ["ddtrace-run", "python", "tests/commands/ddtrace_run_debug.py"],
                stderr=subprocess.STDOUT,
            )
            assert b"Test success" in out
            assert b"DATADOG TRACER CONFIGURATION" in out

    def test_host_port_from_env(self):
        """
        DD_TRACE_AGENT_HOSTNAME|PORT and DATADOG_TRACE_AGENT_HOSTNAME|PORT point to the tracer
        to the correct host/port for submission
        """
        with self.override_env(dict(DATADOG_TRACE_AGENT_HOSTNAME="172.10.0.1", DATADOG_TRACE_AGENT_PORT="8120")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_hostname.py"])
            assert out.startswith(b"Test success")

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

    def test_priority_sampling_from_env(self):
        """
        DD_PRIORITY_SAMPLING and DATADOG_PRIORITY_SAMPLING enable Distributed Sampling
        """
        with self.override_env(dict(DATADOG_PRIORITY_SAMPLING="True")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_priority_sampling.py"])
            assert out.startswith(b"Test success")

        with self.override_env(dict(DD_PRIORITY_SAMPLING="True")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_priority_sampling.py"])
            assert out.startswith(b"Test success")

    def test_patch_modules_from_env(self):
        """
        DATADOG_PATCH_MODULES overrides the defaults for patch_all()
        """
        from ddtrace.bootstrap.sitecustomize import EXTRA_PATCHED_MODULES
        from ddtrace.bootstrap.sitecustomize import update_patched_modules

        orig = EXTRA_PATCHED_MODULES.copy()

        # empty / malformed strings are no-ops
        with self.override_env(dict(DATADOG_PATCH_MODULES="")):
            update_patched_modules()
            assert orig == EXTRA_PATCHED_MODULES

        with self.override_env(dict(DATADOG_PATCH_MODULES=":")):
            update_patched_modules()
            assert orig == EXTRA_PATCHED_MODULES

        with self.override_env(dict(DATADOG_PATCH_MODULES=",")):
            update_patched_modules()
            assert orig == EXTRA_PATCHED_MODULES

        with self.override_env(dict(DATADOG_PATCH_MODULES=",:")):
            update_patched_modules()
            assert orig == EXTRA_PATCHED_MODULES

        # overrides work in either direction
        with self.override_env(dict(DATADOG_PATCH_MODULES="django:false")):
            update_patched_modules()
            assert EXTRA_PATCHED_MODULES["django"] is False

        with self.override_env(dict(DATADOG_PATCH_MODULES="boto:true")):
            update_patched_modules()
            assert EXTRA_PATCHED_MODULES["boto"] is True

        with self.override_env(dict(DATADOG_PATCH_MODULES="django:true,boto:false")):
            update_patched_modules()
            assert EXTRA_PATCHED_MODULES["boto"] is False
            assert EXTRA_PATCHED_MODULES["django"] is True

        with self.override_env(dict(DATADOG_PATCH_MODULES="django:false,boto:true")):
            update_patched_modules()
            assert EXTRA_PATCHED_MODULES["boto"] is True
            assert EXTRA_PATCHED_MODULES["django"] is False

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
            assert out.startswith(b"Test success")

    def test_gevent_patch_all(self):
        with self.override_env(dict(DD_GEVENT_PATCH_ALL="true")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_gevent.py"])
            assert out.startswith(b"Test success")

        with self.override_env(dict(DD_GEVENT_PATCH_ALL="1")):
            out = subprocess.check_output(["ddtrace-run", "python", "tests/commands/ddtrace_run_gevent.py"])
            assert out.startswith(b"Test success")


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

    # For some reason argparse prints the version to stderr
    # in Python 2 and stdout in Python 3
    if PY3:
        assert p.stdout.read() == six.b("ddtrace-run %s\n" % ddtrace.__version__)
    else:
        assert six.b("ddtrace-run %s" % ddtrace.__version__) in p.stderr.read()

    p = subprocess.Popen(
        ["ddtrace-run", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 0
    if PY3:
        assert p.stdout.read() == six.b("ddtrace-run %s\n" % ddtrace.__version__)
    else:
        assert six.b("ddtrace-run %s" % ddtrace.__version__) in p.stderr.read()


def test_bad_executable():
    p = subprocess.Popen(
        ["ddtrace-run", "executable-does-not-exist"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 1
    assert p.stdout.read() == six.b(
        "ddtrace-run: failed to find executable 'executable-does-not-exist'.\n\n"
        "usage: ddtrace-run <your usual python command>\n"
    )


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
    if PY3:
        assert out.startswith(six.b("ddtrace-run: permission error while launching '%s'" % path))
    else:
        assert out.startswith(six.b("ddtrace-run: error launching '%s'" % path))


def test_command_flags():
    out = subprocess.check_output(["ddtrace-run", "python", "-c", "print('test!')"])
    assert out.strip() == six.b("test!")


def test_return_code():
    p = subprocess.Popen(
        ["ddtrace-run", "python", "-c", "import sys;sys.exit(4)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    p.wait()

    assert p.returncode == 4


def test_debug_mode():
    p = subprocess.Popen(
        ["ddtrace-run", "--debug", "python", "-c", "''"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 0
    assert p.stdout.read() == six.b("")
    assert six.b("ddtrace.sampler") in p.stderr.read()


def test_info_no_configs():
    p = subprocess.Popen(
        ["ddtrace-run", "--info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    stdout = p.stdout.read()
    assert (stdout) == (
        b"""\x1b[94m\x1b[1mTracer Configurations:\x1b[0m
    Tracer enabled: True
    Debug logging: False
    Writing traces to: http://localhost:8126
    Agent error: Agent not reachable at http://localhost:8126. """
        + b"""Exception raised: [Errno 99] Cannot assign requested address\n"""
        b"""    App Analytics enabled(deprecated): False
    Log injection enabled: False
    Health metrics enabled: False
    Priority sampling enabled: True
    Partial flushing enabled: False
    Partial flush minimum number of spans: 500
    \x1b[92m\x1b[1mTagging:\x1b[0m
    DD Service: None
    DD Env: None
    DD Version: None
    Global Tags: None
    Tracer Tags: None

\x1b[96m\x1b[1mSummary\x1b[0m"""
        b"""\n\n\x1b[91mERROR: It looks like you have an agent error: 'Agent not reachable at http://localhost:8126."""
        b""" Exception raised: [Errno 99] Cannot assign requested address'\n"""
        b""" If you're experiencing a connection error, please """
        b"""make sure you've followed the setup for your particular environment so that the tracer and Datadog """
        b"""agent are configured properly to connect, and that the Datadog agent is running:"""
        b""" https://ddtrace.readthedocs.io/en/stable/troubleshooting.html"""
        b"""#failed-to-send-traces-connectionrefusederror"""
        b"""\nIf your issue is not a connection error then please reach out to support for further assistance:"""
        b""" https://docs.datadoghq.com/help/\x1b[0m"""
        b"""\n\n\x1b[93mWARNING SERVICE NOT SET: It is recommended that a service tag be set for all traced """
        b"""applications. For more information please see"""
        b""" https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m"""
        b"""\n\n\x1b[93mWARNING ENV NOT SET: It is recommended that an env tag be set for all traced applications. """
        b"""For more information please see https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m"""
        b"""\n\n\x1b[93mWARNING VERSION NOT SET: """
        b"""It is recommended that a version tag be set for all traced applications. """
        b"""For more information please see https://ddtrace.readthedocs.io/en/stable/troubleshooting.html\x1b[0m\n"""
    )

    assert p.returncode == 0


def test_info_w_configs():
    with override_env(
        dict(
            DD_SERVICE="tester",
            DD_ENV="dev",
            DD_VERSION="0.45",
            DD_TRACE_DEBUG="true",
            DD_LOGS_INJECTION="true",
            DD_AGENT_HOST="168.212.226.204",
            DD_TRACER_PARTIAL_FLUSH_ENABLED="true",
            DD_TRACER_PARTIAL_FLUSH_MIN_SPANS="1000",
        )
    ):
        p = subprocess.Popen(
            ["ddtrace-run", "--info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    p.wait()
    stdout = p.stdout.read()
    assert (
        (stdout)
        == b"""\x1b[94m\x1b[1mTracer Configurations:\x1b[0m
    Tracer enabled: True
    Debug logging: True
    Writing traces to: http://168.212.226.204:8126
    Agent error: Agent not reachable at http://168.212.226.204:8126. Exception raised: timed out
    App Analytics enabled(deprecated): False
    Log injection enabled: True
    Health metrics enabled: False
    Priority sampling enabled: True
    Partial flushing enabled: True
    Partial flush minimum number of spans: 1000
    \x1b[92m\x1b[1mTagging:\x1b[0m
    DD Service: tester
    DD Env: dev
    DD Version: 0.45
    Global Tags: None
    Tracer Tags: None

\x1b[96m\x1b[1mSummary\x1b[0m"""
        b"""\n\n\x1b[91mERROR: It looks like you have an agent error: """
        b"""'Agent not reachable at http://168.212.226.204:8126. """
        b"""Exception raised: timed out'\n If you're experiencing a connection error, """
        b"""please make sure you've followed the """
        b"""setup for your particular environment so that the tracer and """
        b"""Datadog agent are configured properly to connect,"""
        b""" and that the Datadog agent is running:"""
        b""" https://ddtrace.readthedocs.io/en/stable/troubleshooting.html#failed-to-send-traces-"""
        b"""connectionrefusederror\n"""
        b"""If your issue is not a connection error then please reach out to support for further assistance: """
        b"""https://docs.datadoghq.com/help/\x1b[0m\n"""
    )

    assert p.returncode == 0


def test_no_args():
    p = subprocess.Popen(
        ["ddtrace-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.returncode == 1
    assert six.b("usage:") in p.stdout.read()
