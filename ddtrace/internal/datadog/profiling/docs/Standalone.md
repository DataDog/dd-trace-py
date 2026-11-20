Standalone Building and Testing
===============================

See the accompanying `Architecture.md` for comments on the high-level design and goals of these directories.
This document discusses some aspects of building and testing the native code in a standalone fashion, apart from the normal dd-trace-py build system.
See `Testing.md` for every check that applies to a profiler change and the CI job that runs it.


Building
--------

The primary consumer of the build system here is setup.py, so many concessions are made with that goal in mind.
A helper script in the parent directory, `build_standalone.sh` can be used to manipulate the build system in a similar manner as `setup.py`, but which leverages the tooling we've added for testing and vetting the native code.


### Why

There are a few reasons why a developer would use `build_standalone.sh`:

* make sure this code builds without having to build other parts of the repo :)
* build and test the native code with sanitizers
* build the code with static analysis tools


### CI

Note that `build_standalone.sh` runs in GitLab's `profiling_native` jobs with the `stack_test` target.
This runs all `dd_wrapper` and stack tests through CTest, with unsanitized, sanitizer, and Valgrind configurations.
See `Testing.md` for the job matrix.


### Notes

Since artifacts, caches, and assets for these builds are stored in a subdirectory in the source tree, they will not interfere with the normal build system.
No need to delete things.
However, you may want to delete things if you switch branches.


### How

`build_standalone.sh` takes exactly three arguments: an option, a build mode, and a target.

* Option: `--` for the default compiler, or one flag such as `--clang`, `--gcc`, `--safety`, `--thread`, `--valgrind`, or `--cppcheck`.
  The help output lists all of them.
  Every option except `--`, `--gcc`, and `--fanalyze` uses clang.
* Build mode: `Debug`, `Release`, or `RelWithDebInfo`. `--` means `Debug`.
* Target: `all`, `stack`, or `ddup`. Each also builds `dd_wrapper`.
  Add `_test` to build and run the tests through CTest.

The script needs bash 4 or later and `bc`.
It first builds the profiling FFI library with `setup.py build_profiling_ffi --inplace`, so run it from the repository root.
Here are the most useful commands.


#### Help
```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh
```


#### Build everything in release mode
```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh -- Release all
```


#### Build using clang

Usually, `setup.py` will use `gcc`, but this can be overridden for testing.

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh --clang -- all
```


#### Build with cppcheck

CPPCheck is a powerful static analysis tool.
It doesn't work very well with cython-generated code, since cython has certain opinions.
It does work pretty well for `dd_wrapper`, though.
The `stack` target runs it on `dd_wrapper` and `stack`. Install `cppcheck` on `PATH` first.

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh --cppcheck -- stack
```


#### Tests

Native tests run through CTest, separately from the Python profiling tests.
They are not built or packaged by `setup.py`.
Add the `_test` suffix to a target name. The `stack_test` target also builds and runs the `dd_wrapper` tests:

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh -- RelWithDebInfo stack_test
```


#### Sanitizers

The code can be built with sanitizers.

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh --safety -- all
```

It can be useful to test with sanitizers enabled.
Disable leak detection, as CI does. The death tests fork, and LeakSanitizer conflicts with GoogleTest in the forked children:

```sh
ASAN_OPTIONS=detect_leaks=0 ./ddtrace/internal/datadog/profiling/build_standalone.sh --safety RelWithDebInfo stack_test
```


#### Valgrind

On Linux, `--valgrind` runs the tests under Valgrind memcheck with the `valgrind.supp` file in each test directory.

```sh
./ddtrace/internal/datadog/profiling/build_standalone.sh --valgrind RelWithDebInfo stack_test
```
