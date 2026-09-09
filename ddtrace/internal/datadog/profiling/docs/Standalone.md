Standalone Building and Testing
===============================

See the accompanying `Design.md` for comments on the high-level design and goals of these directories.
This document discusses some aspects of building and testing the native code in a standalone fashion, apart from the normal dd-trace-py build system.


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


### Notes

Since artifacts, caches, and assets for these builds are stored in a subdirectory in the source tree, they will not interfere with the normal build system.
No need to delete things.
However, you may want to delete things if you switch branches.


### How

`build_standalone.sh` has some online documentation.
Here are the most useful commands.


#### Help
```sh
./build_standalone.sh
```


#### Build everything in release mode
```sh
./build_standalone.sh -- Release all
```


#### Build using clang

Usually, `setup.py` will use `gcc`, but this can be overridden for testing.

```sh
./build_standalone.sh --clang -- all
```


#### Build with cppcheck

CPPCheck is a powerful static analysis tool.
It doesn't work very well with cython-generated code, since cython has certain opinions.
It does work pretty well for `dd_wrapper`, though.

```sh
./build_standalone.sh --cppcheck -- dd_wrapper
```


#### Tests

Native tests run through CTest, separately from the Python profiling tests.
They are not built or packaged by `setup.py`.
Add the `_test` suffix to a target name. The `stack_test` target also builds and runs the `dd_wrapper` tests:

```sh
./build_standalone.sh -- RelWithDebInfo stack_test
```


#### Sanitizers

The code can be built with sanitizers.

```sh
./build_standalone.sh --safety -- all
```

It can be useful to test with sanitizers enabled.

```sh
./build_standalone.sh --safety RelWithDebInfo stack_test
```
