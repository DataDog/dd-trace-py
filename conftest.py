"""
This file configures a local pytest plugin, which allows us to configure plugin hooks to control the
execution of our tests. Either by loading in fixtures, configuring directories to ignore, etc

Local plugins: https://docs.pytest.org/en/3.10.1/writing_plugins.html#local-conftest-plugins
Hook reference: https://docs.pytest.org/en/3.10.1/reference.html#hook-reference
"""
import os
import re
import sys
from time import time

import hypothesis
import pytest


# DEV: Enable "testdir" fixture https://docs.pytest.org/en/stable/reference.html#testdir
pytest_plugins = ("pytester",)

PY_DIR_PATTERN = re.compile(r"^py[23][0-9]$")

# Disable the "too slow" health checks. We are ok if data generation is slow
# https://hypothesis.readthedocs.io/en/latest/healthchecks.html#hypothesis.HealthCheck.too_slow
hypothesis.settings.register_profile("default", suppress_health_check=(hypothesis.HealthCheck.too_slow,))
hypothesis.settings.load_profile("default")


# Hook for dynamic configuration of pytest in CI
# https://docs.pytest.org/en/6.2.1/reference.html#pytest.hookspec.pytest_configure
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        """subprocess(status, out, err, args, env, parametrize, ddtrace_run):
            Mark test functions whose body is to be run as stand-alone Python
            code in a subprocess.

            Arguments:
                status: the expected exit code of the subprocess.
                out: the expected stdout of the subprocess, or None to ignore.
                err: the expected stderr of the subprocess, or None to ignore.
                args: the command line arguments to pass to the subprocess.
                env: the environment variables to override for the subprocess.
                parametrize: whether to parametrize the test function. This is
                    similar to the `parametrize` marker, but arguments are
                    passed to the subprocess via environment variables.
                ddtrace_run: whether to run the test using ddtrace-run.
        """,
    )

    if os.getenv("CI") != "true":
        return

    # Write JUnit xml results to a file that contains this process' PID
    # This ensures running pytest multiple times does not overwrite previous results
    # e.g. test-results/junit.xml -> test-results/junit.1797.xml
    if config.option.xmlpath:
        fname, ext = os.path.splitext(config.option.xmlpath)
        # DEV: `ext` will contain the `.`, e.g. `.xml`
        config.option.xmlpath = "{0}.{1}{2}".format(fname, os.getpid(), ext)

    # Save per-interpreter benchmark results.
    if config.pluginmanager.hasplugin("benchmark"):
        gc = "_nogc" if config.option.benchmark_disable_gc else ""
        config.option.benchmark_save = str(time()).replace(".", "_") + gc + "_py%d_%d" % sys.version_info[:2]


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Attach the outcome of the test (failed, passed, skipped) to the test node so that fixtures
    # can access it.
    # ref: https://stackoverflow.com/a/72629285
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)
