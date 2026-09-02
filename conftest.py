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


def _cgroup(path):
    """Read a cgroup file, or a marker naming the failure.

    These paths are cgroup v2. On a cgroup v1 host the same values live under per-controller
    directories with different names, so they come back as <FileNotFoundError> and only the
    benchmarks stay meaningful. Non-Linux never reaches here, see _env_probe.
    """
    try:
        with open(path) as f:
            return f.read().strip().replace("\n", " | ")
    except Exception as exc:
        return f"<{exc.__class__.__name__}>"


def _env_probe(label):
    """Report what the machine gave this job, so a slow run can be attributed rather than guessed at.

    Called at session start and end; most of the value is in diffing the two. Skipped entirely off
    Linux, where neither the cgroup files nor sched_getaffinity exist; these suites always run in
    the Linux testrunner container, locally and in CI alike.

    affinity / cpu_count
        How many CPUs the process may actually use. Separates "few cores" from "throttled": a job
        pinned to one core and a job throttled to a fraction of 96 both look slow but need
        different fixes. affinity needs sched_getaffinity, which is Linux only.

    cpu.max
        The CFS quota and period, e.g. "25000 100000" for a quarter core. A literal "max" means no
        quota at all, which also means quota throttling cannot be happening, so the counters below
        will stay at zero however contended the node is.

    cpu.stat
        usage_usec is the CPU time the container has consumed. Diffing it between the two probes and
        comparing against wall clock gives utilisation, which is the one number that separates
        "computing hard" from "sitting in a blocking call". A job at 24% is waiting on something;
        a job near 100% is genuinely compute bound. nr_throttled and throttled_usec attribute any
        stalling to quota enforcement, and are only ever non-zero when cpu.max sets a quota.

    memory.max / memory.current
        The limit this pod was given, against what it actually used. Worth watching where limits are
        autoscaled rather than declared: a peak sitting close to the limit predicts intermittent
        OOM kills, which surface as flaky infrastructure failures rather than test failures.

    BENCH cpu_loop_5M
        Pure-Python arithmetic throughput, no syscalls or allocation. The baseline for comparing one
        runner against another, or against a laptop. Expect it to vary by more than 2x between
        runners in the same pipeline, so read single-shard timings with that in mind.

    BENCH syscall_getpid_200k
        The cost of entering the kernel, using about the cheapest syscall there is. Isolates syscall
        overhead from the work done in a syscall, which matters under seccomp or gVisor style
        sandboxing.

    BENCH stat_20k
        Filesystem metadata cost. Sensitive to how many overlay and bind mount layers the checkout
        sits behind, which is usually the difference between a container and a host filesystem.
    """
    if not sys.platform.startswith("linux"):
        return

    import time as _time

    lines = [f"===== RUNTIME PROBE [{label}] ====="]
    lines.append(f"affinity={len(os.sched_getaffinity(0))} cpu_count={os.cpu_count()}")
    for path in (
        "/sys/fs/cgroup/cpu.max",
        "/sys/fs/cgroup/cpu.stat",
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory.current",
    ):
        lines.append(f"{path} = {_cgroup(path)}")

    t0 = _time.perf_counter()
    acc = 0
    for i in range(5_000_000):
        acc += i * i
    lines.append(f"BENCH cpu_loop_5M = {_time.perf_counter() - t0:.3f}s")

    t0 = _time.perf_counter()
    for _ in range(200_000):
        os.getpid()
    lines.append(f"BENCH syscall_getpid_200k = {_time.perf_counter() - t0:.3f}s")

    t0 = _time.perf_counter()
    for _ in range(20_000):
        os.stat(__file__)
    lines.append(f"BENCH stat_20k = {_time.perf_counter() - t0:.3f}s")

    lines.append("===== END RUNTIME PROBE =====")
    print("\n" + "\n".join(lines), flush=True)


def _probe_enabled(config):
    """Probe once per session on the xdist controller only, in CI or when explicitly asked."""
    if hasattr(config, "workerinput"):
        return False
    return os.getenv("CI") == "true" or os.getenv("DD_TEST_RUNTIME_PROBE") == "1"


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

    if _probe_enabled(config):
        _env_probe("session start")

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


@pytest.fixture(scope="session", autouse=True)
def collect_global_attributes(record_testsuite_property, pytestconfig):
    randomly_seed = pytestconfig.getoption("randomly_seed", None)
    # Record the random seed used for this test run.
    # This is useful to reproduce test failures.
    record_testsuite_property("randomly.seed", f"{randomly_seed or -1}")

    # Convert all RIOT_* variables to `riot.*` attributes in the test suite.
    # https://github.com/DataDog/riot/blob/a412e98fe6194284b97235942a6e3eff7e8d0a0b/riot/riot.py#L786-L797
    for env, value in os.environ.items():
        if not env.startswith("RIOT_"):
            continue

        # Convert:
        #   - RIOT_NAME into `riot.name`
        #   - RIOT_VENV_PKGS into `riot.venv.pkgs`
        #   - RIOT_VENV_FULL_PKGS into `riot.venv.full_pkgs`
        #   - RIOT_PYTHON_VERSION into `riot.python.version`
        env = env[5:]  # Remove "RIOT_" prefix
        prefix, _, name = env.partition("_")
        if prefix and name:
            # Convert `RIOT_VENV_PKGS` to `riot.venv.pkgs`
            env = f"riot.{prefix.lower()}.{name.lower()}"
        else:
            env = f"riot.{prefix.lower()}"
        record_testsuite_property(env, value)


def pytest_sessionfinish(session, exitstatus):
    if _probe_enabled(session.config):
        _env_probe("session end")
