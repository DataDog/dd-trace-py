Building and Testing the Profiler
=================================

This guide lists every way to build and check a change to the Python profiler.
For each check it gives the command, what the check covers, and the CI job that runs it.
The profiler code lives in `ddtrace/profiling/`, `ddtrace/internal/profiling/`, and `ddtrace/internal/datadog/profiling/`.
Native profiler code also depends on the Rust extension in `src/native/`.


Building
--------

### In-tree build

`pip install -e .` builds every native extension, including the profiler's.
See [docs/build_system.rst](../../../../../docs/build_system.rst) for `DD_COMPILE_MODE`, `DD_FAST_BUILD`, and caching.
You do not need to run it for Python tests: `scripts/run-tests` builds ddtrace in each test environment.

### Standalone native build

`build_standalone.sh` builds `dd_wrapper`, `ddup`, and `stack` (which includes echion) with CMake, outside `setup.py`.
It is the entry point for native unit tests, sanitizers, Valgrind, and static analysis.
It always takes three arguments: an option, a build mode, and a target.

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh <option> <build mode> <target>
```

See [Standalone.md](Standalone.md) for the options, modes, and targets.


Checks
------

### Python test suites

```sh
scripts/run-tests --list <changed files>
scripts/run-tests --venv <hash> -- -k <test name>
scripts/run-tests -r --venv <hash>   # after native changes
```

The suites are defined in [tests/profiling/suitespec.yml](../../../../../tests/profiling/suitespec.yml).
Each runs in GitLab as a job named `profiling/<suite>`.
On a pull request, a suite runs when the changed files match its paths. On `main`, every suite runs.
The tests run in the Docker testrunner container and set `DD_PROFILING_ENABLE_ASSERTS=1`.
The `run-tests` skill (`.agents/skills/run-tests/`) explains environment selection.

| Suite | Covers | Python |
|-------|--------|--------|
| `profile` | All of `tests/profiling/` except memalloc. Includes subprocess, fork, asyncio, gunicorn, and uWSGI tests. | 3.9 to 3.15 |
| `profile` gevent variant | The same tests with `DD_PROFILE_TEST_GEVENT=1`, which enables `test_gevent.py` and gevent workers in `test_gunicorn.py` and `test_uwsgi.py`. Pick a hash whose packages include `gevent`. | 3.9 to 3.15 |
| `profile` uvloop variant | The same tests with `USE_UVLOOP=1`. | 3.9 to 3.15 |
| `profile-uwsgi` | `test_uwsgi.py` with `uwsgi<2.0.30`. | 3.9 to 3.13 |
| `profile-memalloc` | `collector/test_memalloc.py` and `test_memalloc_fork.py`, run once per `PYTHONMALLOC` mode (`malloc`, `pymalloc`, `malloc_debug`, `pymalloc_debug`). | 3.9 to 3.15 |

Many tests use `@pytest.mark.subprocess` and run in a fresh interpreter.
The `crashtracker` suite (`tests/crashtracker/`) also runs on profiler changes, as the GitLab job `core/crashtracker`.

### Native unit tests

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh -- RelWithDebInfo stack_test
```

This builds the Rust extension, then runs the GoogleTest suites in `dd_wrapper/test/` and `stack/test/` through CTest.
The `stack_test` target also runs the `dd_wrapper` tests.
Native tests are not part of the Python suites, and the memalloc extension (`ddtrace/profiling/collector/_memalloc*`) has no native tests.

CI job: `profiling_native` in [.gitlab-ci.yml](../../../../../.gitlab-ci.yml).
It runs `build_standalone.sh --${SANITIZER} RelWithDebInfo stack_test` in the profiling native image.
The matrix is Python 3.9 to 3.15 with `SANITIZER` set to `safety`, `thread`, or empty (no sanitizer), plus `valgrind` on 3.12 and 3.15.
It runs on `main`, on schedules, and when native profiler, Rust, or build files change.

### Sanitizers and Valgrind

```sh
ASAN_OPTIONS=detect_leaks=0 ./ddtrace/internal/datadog/profiling/build_standalone.sh --safety RelWithDebInfo stack_test
./ddtrace/internal/datadog/profiling/build_standalone.sh --thread RelWithDebInfo stack_test
./ddtrace/internal/datadog/profiling/build_standalone.sh --valgrind RelWithDebInfo stack_test
```

`--safety` builds with clang and AddressSanitizer, LeakSanitizer, and UndefinedBehaviorSanitizer.
Set `ASAN_OPTIONS=detect_leaks=0` for it, as CI does. Without it, LeakSanitizer fails the run.
CI disables leak detection because the GoogleTest death tests fork, and LeakSanitizer conflicts with GoogleTest in the forked children.
The Valgrind runs check for leaks instead.

`--thread` builds with ThreadSanitizer. CMake sets `TSAN_OPTIONS` with the suppressions in `dd_wrapper/test/TSan.supp`.
In Docker, ThreadSanitizer needs `--security-opt seccomp=unconfined`, because it calls `personality()` to disable address randomization.

`--valgrind` runs CTest under `valgrind` memcheck with the `valgrind.supp` files in each test directory. It needs Linux.
Run it on amd64. On arm64, such as Docker on Apple silicon, it reports uninitialized values in Rust regex code.

The same `profiling_native` matrix runs all three in CI.

### Static analysis

```sh
./ddtrace/internal/datadog/profiling/run_clang_tidy.sh
```

This runs clang-tidy on `dd_wrapper`, `stack` (including the fuzz harnesses), and memalloc.
CI job: `clang-tidy profiling` in [.gitlab/native.yml](../../../../../.gitlab/native.yml), on every pipeline.

`build_standalone.sh --cppcheck -- stack` runs cppcheck on `dd_wrapper` and `stack`. It needs `cppcheck` on `PATH`.
No CI job runs it, and it fails on current findings in `dd_wrapper`.

### Rust

`cargo test`, `cargo clippy`, and `cargo fmt --check` for `src/native/` and `src/native_heap_gotter/`.
CI job: `rust ci` in [.gitlab/native.yml](../../../../../.gitlab/native.yml), on every pipeline.

### Wheels

`build linux` (manylinux2014 and musllinux, x86_64 and aarch64), `build macos amd64`, and `build macos arm64` in [.gitlab/package.yml](../../../../../.gitlab/package.yml) build the wheels for every supported Python version, on every pipeline.
Linux wheels go through `auditwheel repair`, so a library feature that manylinux2014 does not allow builds in-tree but fails here.
See the manylinux2014 decision in [Architecture.md](Architecture.md#design-decisions).

### CI trigger coverage

```sh
scripts/lint profiling-native-check
```

[scripts/check_profiling_native_coverage.py](../../../../../scripts/check_profiling_native_coverage.py) checks that the `profiling_native` `rules:changes` patterns match every native file and no other file.
Run it after you add a native file type or move native files.
The GitLab `prechecks` job runs it as `Check profiling native coverage` when `.gitlab-ci.yml` or profiler paths change.

### Fuzzing

The echion unwinder has libFuzzer targets in `stack/fuzz/`.
See [stack/fuzz/README.md](../stack/fuzz/README.md) to build and run them.
CI job: `fuzz_infra` in [.gitlab/fuzz.yml](../../../../../.gitlab/fuzz.yml), on Python 3.9 to 3.14.
It runs on nightly pipelines and manually on branches. It does not block merges.

### CPython versions

echion reads CPython internal structures directly from memory.
Their layout changes between minor versions, and the code has `PY_VERSION_HEX` branches for each version from 3.9 to 3.15.
Run the native and Python tests on every version whose branch your change touches, and on the oldest and newest supported versions.
CI runs every supported version (3.9 to 3.15).
To find the CPython internals the profiler uses, or what changed between two versions, use the `find-cpython-usage` and `compare-cpython-versions` skills in `.agents/skills/`.

### End-to-end correctness

The `DataDog/prof-correctness` repository runs sample applications under the profiler and checks the profiles they produce.
Two jobs trigger its `downstream-python.yml` workflow against the wheels CI built for the commit.
Both run the default scenarios `python_(mem_domain|exceptions|async_gen|lock)_3\.(14|15)` and do not block merges.

- GitHub Actions: [.github/workflows/prof-correctness.yml](../../../../../.github/workflows/prof-correctness.yml).
  It runs on pull requests that change profiler paths or `src/native/`.
  It waits up to 90 minutes for the wheels to appear in S3, then triggers the downstream run and waits for it.
  It skips fork pull requests.
  `workflow_dispatch` accepts `commit_sha` and `test_scenarios` to run other scenarios or commits.
- GitLab: the `prof-correctness` job in [.gitlab-ci.yml](../../../../../.gitlab-ci.yml).
  It runs after `upload all`, on `main`, on schedules, and on pushes that change profiler paths or `src/native/`.
  Otherwise it is manual.

### GPU

[.github/workflows/pytorch_gpu_tests.yml](../../../../../.github/workflows/pytorch_gpu_tests.yml) runs `tests/profiling/test_pytorch.py` on a GPU runner.
It installs the CI-built Python 3.12 x86_64 wheel and sets `DD_PROFILING_PYTORCH_ENABLED=true`.
It runs on pull requests and `main` pushes that change `ddtrace/profiling/` or `ddtrace/internal/datadog/profiling/`.
It is not a required check. It cannot run locally without an NVIDIA GPU.

### Performance

```sh
scripts/run-benchmarks --list <changed files>
scripts/run-benchmarks --scenario <scenario> --artifacts ./benchmark-artifacts/
```

The `run-benchmarks` skill (`.agents/skills/run-benchmarks/`) explains baselines and analysis.
Only two microbenchmarks target profiler code: `exception_profiler_monitoring` (the exception collector) and `code_provenance`.
Four other scenarios have one config with the profiler enabled: `django_simple` (`profiler`), `flask_simple` (`profiler`), `recursive_computation` (`deep-profiled`), and `startup` (`ddtrace_run_profiling`).
No microbenchmark isolates the stack sampler, the memory collector, or the lock collector.
Datadog maintainers also run internal benchmark suites.

CI job: `microbenchmarks`, for the scenarios that `scripts/run-benchmarks --list` reports for the change.
The `check-slo-breaches` gate blocks merges on the thresholds in [.gitlab/benchmarks/slos/profiling-python.yml](../../../../../.gitlab/benchmarks/slos/profiling-python.yml).
The `macrobenchmarks` job has profiling-enabled configs. It runs on nightly pipelines and release branches, and manually otherwise.


CI jobs
-------

| Check | CI job | Runs on | Blocks merge |
|-------|--------|---------|--------------|
| Python suites | GitLab `profiling/profile`, `profiling/profile-uwsgi`, `profiling/profile-memalloc`, `core/crashtracker` | Matching path changes, `main` | Yes |
| Native unit tests, sanitizers, Valgrind | GitLab `profiling_native` | Native path changes, `main`, schedules | Yes |
| clang-tidy | GitLab `clang-tidy profiling` | Every pipeline | Yes |
| Rust | GitLab `rust ci` | Every pipeline | Yes |
| Wheels | GitLab `build linux`, `build macos amd64`, `build macos arm64` | Every pipeline | Yes |
| CI trigger coverage | GitLab `prechecks` (`Check profiling native coverage`) | Profiler or CI config changes | Yes |
| Fuzzing | GitLab `fuzz_infra` | Nightly, manual | No |
| End-to-end correctness | GitHub `Profiling correctness`, GitLab `prof-correctness` | Profiler path changes | No |
| GPU | GitHub `Pytorch Unit Tests (with GPU)` | Profiler path changes | No |
| Microbenchmarks | GitLab `microbenchmarks`, `check-slo-breaches` | Matching path changes | Yes, on SLO breach |
| Macrobenchmarks | GitLab `macrobenchmarks` | Nightly, release branches, manual | No, on pull requests |
