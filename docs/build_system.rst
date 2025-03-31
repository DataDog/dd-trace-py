.. _`build system`:

Build System
============

Native Pre-Dependencies
~~~~~~~~~~~~~~~~~~~~~~~

Some native extensions are written in Rust, Cython, or C++, requiring specific dependencies to build them.

For Rust, you need the `rustc` and `cargo` tools installed. Install them by following these steps:

.. code-block:: bash

    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- --default-toolchain stable -y

For C++, requirements vary by system, but you’ll need a C++ compiler and `cmake`. Install them as follows:

- On Ubuntu:

.. code-block:: bash

    sudo apt-get install build-essential cmake

- On macOS:

.. code-block:: bash

    brew install cmake
    xcode-select --install

Build Dependencies
~~~~~~~~~~~~~~~~~~

To see the current build dependencies, check the `[build-system]` section in the `pyproject.toml` file. For example, at the time of writing, the dependencies are:

.. code-block:: toml

    [build-system]
    requires = ["setuptools_scm[toml]>=4", "cython", "cmake>=3.24.2,<3.28; python_version>='3.8'", "setuptools-rust<2"]

To install all dependencies in one step, use:

.. code-block:: bash

    pip install . --no-build-isolation --no-install

Note that `pip install -e` (described below) also installs these build dependencies automatically.

Local Installation from a Local Repository
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The simplest way to build and install the library in your Python environment is with `pip`:

.. code-block:: bash

    pip install .

Local Installation in Development Mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To install the library in development mode, use the `-e` flag:

.. code-block:: bash

    pip install -e .

This installs the library in "editable" mode, meaning changes to the source code are immediately reflected in the installed package.
This works only if you run the command from the repository’s root directory. To use `ddtrace` from a different directory (e.g., another
project running under `ddtrace`), add the repository path to your `PYTHONPATH` environment variable:

.. code-block:: bash

    export PYTHONPATH=/path/to/ddtrace:$PYTHONPATH

If you skip `pip install -e .` and use `PYTHONPATH`, you must manually install the build dependencies (see above) and compile the native extensions with:

.. code-block:: bash

    python setup.py build_ext --inplace

Then, if you want to run ddtrace from the repo with another project in another directory, instead of using `ddtrace-run` (which
with an editable or `PYTHONPATH` install would not find the one in the repository), do:

.. code-block:: bash

    python -m ddtrace.commands.ddtrace_run python my_traced_app.py

Using `sccache` to Speed Up Builds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you frequently rebuild native extensions or deploy to multiple containers, consider using `sccache` to accelerate compilation.
`sccache` caches compilation results, reducing build times. Install it using `cargo`, which you should have if you followed the Rust installation steps earlier:

.. code-block:: bash

    cargo install sccache

For the build system to locate `sccache`, either ensure its path is in your `PATH` environment variable or set the `SCCACHE` environment variable to its location:

.. code-block:: bash

    export SCCACHE=/home/doe/.cargo/bin/sccache

Additionally, enable `sccache` by setting the `DD_USE_SCCACHE` environment variable:

.. code-block:: bash

    export DD_USE_SCCACHE=1

Then, build the tracer as usual.

To verify that `sccache` is working after a build, check its statistics:

.. code-block:: bash

    sccache --show-stats

If `cache-hits` is greater than 0 (or increases after a cached build), `sccache` is functioning correctly.

To change the `sccache` cache directory—e.g., to share it with containers via copying or mounting a volume—set the `SCCACHE_DIR` environment variable:

.. code-block:: bash

    export SCCACHE_DIR=/path/to/cache

The `sccache --show-stats` command also displays the current cache directory.

Configuration Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These environment variables modify aspects of the build process.

.. ddtrace-configuration-options::
  DD_COMPILE_DEBUG (DEPRECATED):
    type: Boolean
    default: False

    description: |
        This deprecated variable will be removed in a future release.
        If set to 1, it compiles the tracer with debug symbols—useful for debugging the tracer itself but resulting in a slower and larger binary.
        This is not recommended for production. If set to 0, extensions are compiled in `Release` mode.

    version_added:
        v0.44.0:

  DD_USE_SCCACHE:
    type: Boolean
    default: False

    description: |
        If set to 1, it enables `sccache` to accelerate native extension compilation (see above). This is beneficial for frequent rebuilds
        or multi-container deployments.

    version_added:
        v2.12.0:

  DD_COMPILE_MODE:
    type: String
    default: Release

    description: |
        Specifies the compilation mode for native extensions. Depending on your CMake version, options typically include `Release`, `Debug`,
        `RelWithDebInfo`, and `MinSizeRel`. Note that `Debug` produces slower, larger binaries; `RelWithDebInfo` increases size but retains the performance of `Release`;
        and `MinSizeRel` reduces binary size at the cost of performance.

    version_added:
        v3.3.0:

  DD_COMPILE_ABSEIL:
    type: Boolean
    default: True

    description: |
        If set to 1, the tracer is compiled with the Abseil library, enhancing performance for Runtime Code Analysis features when active
        (`DD_IAST_ENABLED=1`). If set to 0, the Runtime Code Analysis extension is built without Abseil, speeding up the build process at the cost of
        some performance.

    version_added:
        v3.3.0:

  DD_FAST_BUILD:
    type: Boolean
    default: False

    description: |
        If set to 1, the tracer is compiled with minimal optimizations (`-g0`) and `DD_COMPILE_ABSEIL` is forced to 0. This is not recommended
        for production due to reduced performance.

    version_added:
        v3.3.0:

  DD_PROFILING_NATIVE_TESTS:
    type: Boolean
    default: False

    description: |
        If set to 1, it compiles the profiling native tests. This is useful only when modifying the library’s profiling features and
        is disabled by default.

    version_added:
        v2.16.0:

  DD_BUILD_EXT_INCLUDES:
    type: String
    default: ""

    description: |
        Comma separated list of ``fnmatch`` patterns for native extensions to build when installing the package from source.
        Example: ``DD_BUILD_EXT_INCLUDES="ddtrace.internal.*" pip install -e .`` to only build native extensions found in ``ddtrace/internal/`` folder.

        ``DD_BUILD_EXT_EXCLUDES`` takes precedence over ``DD_BUILD_EXT_INCLUDES``.

    version_added:
        v3.3.0:

  DD_BUILD_EXT_EXCLUDES:
    type: String
    default: ""

    description: |
        Comma separated list of ``fnmatch`` patterns for native extensions to skip when installing the package from source.
        Example: ``DD_BUILD_EXT_EXCLUDES="*._encoding" pip install -e .`` to build all native extensions except ``ddtrace.internal._encoding``.

        ``DD_BUILD_EXT_EXCLUDES`` takes precedence over ``DD_BUILD_EXT_INCLUDES``.

    version_added:
        v3.3.0:
