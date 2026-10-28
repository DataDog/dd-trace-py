from collections.abc import Callable
from collections.abc import Generator
import os
from pathlib import Path
import subprocess  # nosec B404
import sys
import tempfile

import bm


class PytestPlugin(bm.Scenario):  # type: ignore[misc]
    """Macrobenchmark for the ddtrace pytest (CI Visibility / Test Visibility) plugin.

    Runs a generated corpus of trivial assert-True tests through pytest, measuring
    whole-session wall time for pure pytest, the ddtrace plugin, and the plugin with
    forced TIA file-level coverage. Runs use hermetic payload-files mode so no network
    is involved. This isolates synchronous product overhead such as span lifecycle,
    source discovery, per-test coverage contexts, and telemetry.

    The benchmark platform compares a baseline ddtrace build against the candidate
    for each config. The pure-pytest and plugin-without-coverage configurations act as
    controls for the coverage result.
    """

    ntests: int
    nmodules: int
    ddtrace: bool
    coverage: bool

    # Subprocess benchmark: cProfile of the harness process would not attribute the
    # child's work, so disable the cProfile pstats generation.
    cprofile_loops: int = 0

    def run(self) -> Generator[Callable[[int], None], None, None]:
        # Build a fresh corpus for this measurement. Setup runs before the yield, so
        # it is outside the pyperf-timed region (only the yielded callable is timed).
        workdir = tempfile.mkdtemp(prefix="ddbench_pytest_")
        corpus = Path(workdir) / "tests"
        corpus.mkdir(parents=True, exist_ok=True)
        per_module = max(1, self.ntests // max(1, self.nmodules))
        for m in range(self.nmodules):
            with open(corpus / f"test_mod_{m}.py", "w") as f:
                for i in range(per_module):
                    f.write(f"def test_{i:04d}():\n    assert True\n\n")

        payload_dir = Path(workdir) / "payloads"
        payload_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        # Hermetic offline mode: payloads go to files instead of HTTP. This keeps
        # the per-test product path while removing network variance.
        env.update(
            {
                "DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES": "true",
                "TEST_UNDECLARED_OUTPUTS_DIR": str(payload_dir),
                # Provide static git metadata so session start does not attempt to discover
                # or upload a real repository (which would also touch the network).
                "DD_GIT_REPOSITORY_URL": "https://github.com/example/ddbench",
                "DD_GIT_COMMIT_SHA": "01234567890abcdef01234567890abcdef0123456",
                "DD_GIT_BRANCH": "main",
                # Force the real TIA per-test collection path without a backend settings
                # response. File-level mode exercises PY_START on Python 3.12+.
                "_DD_CIVISIBILITY_ITR_FORCE_ENABLE_COVERAGE": "true" if self.coverage else "false",
                "_DD_COVERAGE_FILE_LEVEL": "true" if self.coverage else "false",
            }
        )

        args = [
            sys.executable,
            "-m",
            "pytest",
            str(corpus),
            "-q",
            # pytest-randomly (if installed) asserts config.cache is not None,
            # so we disable it rather than disabling the cache provider.
            "-p",
            "no:randomly",
            "--rootdir",
            str(corpus),
        ]
        if self.ddtrace:
            args.append("--ddtrace")
        else:
            # Explicitly disable the ddtrace pytest plugin so the baseline config
            # measures pure pytest, not just pytest without --ddtrace (the plugin
            # still loads and registers hooks unless deactivated).
            args.append("-p")
            args.append("no:ddtrace")

        def _(loops: int) -> None:
            for _ in range(loops):
                result = subprocess.run(  # nosec B603
                    args,
                    env=env,
                    cwd=corpus,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"pytest exited {result.returncode}:\n{result.stderr.decode()[-1000:]}")

        try:
            yield _
        finally:
            # Clean up the corpus and payload files so repeated pyperf samples do
            # not accumulate in /tmp and perturb later measurements.
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)
