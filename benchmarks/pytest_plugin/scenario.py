import os
import subprocess
import sys
import tempfile

import bm


class PytestPlugin(bm.Scenario):
    """Macrobenchmark for the ddtrace pytest (CI Visibility / Test Visibility) plugin.

    Runs a generated corpus of trivial ``assert True`` tests through pytest, with and
    without ``--ddtrace``, measuring whole-session wall time. The ddtrace run uses the
    plugin's hermetic offline (payload-files) mode so no network is involved: the
    backend connector is NoOp (all features off) and event payloads are written to a
    temp directory instead of being sent over HTTP. This isolates the synchronous
    per-test overhead — span lifecycle, source-location discovery, the coverage
    context manager, and per-test telemetry — which is what adoption-sensitive
    customers pay for fast unit-test suites.

    The benchmark platform compares a baseline ddtrace (PyPI) against the candidate
    (local) build for each config, so a fix that lowers per-test overhead shows up as
    a faster candidate session for the ``ddtrace`` config. The ``baseline`` config
    (no ``--ddtrace``) is a control: the plugin is loaded but never activated, so
    both versions should match.
    """

    ntests: int
    nmodules: int
    ddtrace: bool

    # Subprocess benchmark: cProfile of the harness process would not attribute the
    # child's work, so disable the cProfile pstats generation.
    cprofile_loops: int = 0

    def run(self):
        # Build a fresh corpus for this measurement. Setup runs before the yield, so
        # it is outside the pyperf-timed region (only the yielded callable is timed).
        workdir = tempfile.mkdtemp(prefix="ddbench_pytest_")
        corpus = os.path.join(workdir, "tests")
        os.makedirs(corpus, exist_ok=True)
        per_module = max(1, self.ntests // max(1, self.nmodules))
        for m in range(self.nmodules):
            with open(os.path.join(corpus, f"test_mod_{m}.py"), "w") as f:
                for i in range(per_module):
                    f.write(f"def test_{i:04d}():\n    assert True\n\n")

        payload_dir = os.path.join(workdir, "payloads")
        os.makedirs(payload_dir, exist_ok=True)

        env = os.environ.copy()
        # Hermetic offline mode: NoOp backend connector (all features off), payloads to
        # files instead of HTTP. Keeps the per-test path identical to a real agentless
        # run while removing all network variance.
        env.update(
            {
                "DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES": "true",
                "TEST_UNDECLARED_OUTPUTS_DIR": payload_dir,
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
                # Provide static git metadata so session start does not attempt to discover
                # or upload a real repository (which would also touch the network).
                "DD_GIT_REPOSITORY_URL": "https://github.com/example/ddbench",
                "DD_GIT_COMMIT_SHA": "01234567890abcdef01234567890abcdef0123456",
                "DD_GIT_BRANCH": "main",
            }
        )

        args = [
            sys.executable,
            "-m",
            "pytest",
            corpus,
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            corpus,
        ]
        if self.ddtrace:
            args.append("--ddtrace")

        def _(loops: int):
            for _ in range(loops):
                result = subprocess.run(
                    args,
                    env=env,
                    cwd=corpus,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        "pytest exited {}:\n{}".format(result.returncode, result.stderr.decode()[-1000:])
                    )

        yield _
