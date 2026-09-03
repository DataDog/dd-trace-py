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


def _cgroup_root():
    """Resolve this process's cgroup v2 directory, and which hierarchy version is in use.

    /sys/fs/cgroup is the job's own cgroup only under a private cgroup namespace; where the host
    hierarchy is exposed the job sits under a subpath and the mount root describes the whole node.
    """
    rel, version, entries = "", "unknown", []
    try:
        with open("/proc/self/cgroup") as f:
            entries = [x.strip() for x in f if x.strip()]
    except Exception:  # nosec
        pass
    unified = [e for e in entries if e.startswith("0::")]
    if unified:
        rel, version = unified[0][3:], "v2"
    elif entries:
        version = "v1"
    mount = None
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                if right.startswith("cgroup2 ") and len(left.split()) > 4:
                    mount = left.split()[4]
                    break
    except Exception:  # nosec
        pass
    if mount is None:
        return None, version, "no cgroup2 mount"
    candidate = mount + rel if rel not in ("", "/") else mount
    if os.path.isdir(candidate):
        return candidate, version, "resolved"
    return mount, version, f"mount root, {candidate} absent"


def _cgroup(root, name):
    if root is None:
        return "<no cgroup2>"
    try:
        with open(os.path.join(root, name)) as f:
            return f.read().strip().replace("\n", " | ")
    except Exception as exc:
        return f"<{exc.__class__.__name__}>"


def _safe(fn, label):
    try:
        return fn()
    except Exception as exc:
        return f"{label} = <{exc.__class__.__name__}: {exc}>"


def _benchmark_lines():
    import time as _time

    def cpu():
        acc = 0
        for i in range(5_000_000):
            acc += i * i

    def getpid():
        for _ in range(200_000):
            os.getpid()

    def stat():
        for _ in range(20_000):
            os.stat(__file__)

    def timed(label, fn):
        def run():
            t0 = _time.perf_counter()
            fn()
            return f"BENCH {label} = {_time.perf_counter() - t0:.3f}s"

        return _safe(run, f"BENCH {label}")

    return [timed("cpu_loop_5M", cpu), timed("syscall_getpid_200k", getpid), timed("stat_20k", stat)]


def _resource_lines():
    import time as _time

    out = [f"monotonic = {_time.monotonic():.3f}s", _safe(lambda: f"cpu_count = {os.cpu_count()}", "cpu_count")]
    if not sys.platform.startswith("linux"):
        return out + ["affinity = <non-linux>", "cgroup = <non-linux>"]
    out.append(_safe(lambda: f"affinity = {len(os.sched_getaffinity(0))}", "affinity"))
    root, version, note = _cgroup_root()
    out.append(f"cgroup = {root} ({version}, {note})")
    return out + [
        f"{n} = {_cgroup(root, n)}"
        for n in ("cpu.max", "cpu.stat", "memory.max", "memory.current", "memory.peak", "memory.events")
    ]


def _env_probe(label, benchmarks_first):
    """Report what the machine gave this job, so a slow run can be attributed rather than guessed at.

    Called at session start and end; most of the value is in diffing the two. Benchmarks run before
    the snapshot at start and after it at end, so the two snapshots bracket the test session alone
    and the probe's own CPU burn stays out of the delta.

    monotonic
        Clock reading taken with the snapshot, so utilisation is delta(usage_usec) over
        delta(monotonic), recomputable from the log rather than guessed from job timestamps.

    affinity / cpu_count
        How many CPUs the job may actually use. Separates "few cores" from "throttled": a job pinned
        to one core and a job throttled to a fraction of 96 both look slow but need different fixes.
        affinity is Linux only; off Linux both it and the cgroup values report <non-linux>.

    cgroup
        The resolved cgroup directory, its version, and how it was found. Everything below is read
        from there. On cgroup v1 those files do not exist, so only the benchmarks stay meaningful.

    cpu.max
        The CFS quota and period for this cgroup, e.g. "25000 100000" for a quarter core. A literal
        "max" means this cgroup sets no quota, but an ancestor still can, so read nr_throttled
        rather than concluding throttling is impossible.

    cpu.stat
        usage_usec is CPU time consumed. Against monotonic it gives utilisation, the one number
        separating "computing hard" from "sitting in a blocking call": 24% is waiting on something,
        near 100% is compute bound. nr_throttled and throttled_usec attribute stalling to quota.

    memory.max / memory.current / memory.peak / memory.events
        Limit granted, usage right now, high-water mark, and limit-event counters. memory.current is
        a point reading and misses spikes between snapshots; memory.peak is what to compare against
        the limit, and a peak close to it predicts OOM kills, which surface as infrastructure
        failures rather than test failures.

    BENCH cpu_loop_5M
        Pure-Python arithmetic throughput, no syscalls. The baseline for comparing one runner against
        another. Expect more than 2x spread between runners in one pipeline.

    BENCH syscall_getpid_200k
        Cost of entering the kernel, using about the cheapest syscall there is. Isolates syscall
        overhead, which matters under seccomp or gVisor style sandboxing.

    BENCH stat_20k
        Filesystem metadata cost. Sensitive to how many overlay and bind mount layers the checkout
        sits behind, usually the difference between a container and a host filesystem.
    """
    order = (_benchmark_lines(), _resource_lines()) if benchmarks_first else (_resource_lines(), _benchmark_lines())
    lines = [f"===== RUNTIME PROBE [{label}] ====="] + order[0] + order[1] + ["===== END RUNTIME PROBE ====="]
    print("\n" + "\n".join(lines), flush=True)


def _probe(config, label, benchmarks_first):
    """Never let a diagnostic change the outcome of a run that would otherwise have passed."""
    if not _probe_enabled(config):
        return
    try:
        _env_probe(label, benchmarks_first)
    except BaseException as exc:  # noqa: BLE001
        print(f"\n<runtime probe failed: {exc.__class__.__name__}: {exc}>", flush=True)


def _probe_enabled(config):
    """Probe once per session on the xdist controller only, in CI or when explicitly asked."""
    if hasattr(config, "workerinput") or os.getenv("DD_TEST_RUNTIME_PROBE") == "0":
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

    _probe(config, "session start", benchmarks_first=True)

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
    _probe(session.config, "session end", benchmarks_first=False)
