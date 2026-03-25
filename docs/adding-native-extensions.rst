.. _adding-native-extensions:

Adding Native Extensions
========================

This guide covers how to add new native extensions to ddtrace.  There are three
extension types, each with its own pattern:

- :ref:`cython-extensions` — ``.pyx`` files compiled to C via Cython
- :ref:`c-cpp-extensions` — plain C or C++ files compiled directly
- :ref:`rust-extensions` — Rust code compiled via Cargo/Corrosion into the
  ``_native`` cdylib

Read the :ref:`build system <build system>` page first if you haven't already.
Always consult ``docs/native-code-review.md`` before submitting a PR that adds
or modifies native code.

.. _cython-extensions:

Cython Extensions
-----------------

Cython extensions are the most common way to expose Python-callable C-speed
code.  The build system provides a ``dd_add_cython_ext()`` CMake helper that
handles the full pipeline: ``.pyx → .c → .so``.

**Step 1 — Write the ``.pyx`` file.**

Place it anywhere under the ``ddtrace/`` source tree next to its pure-Python
counterpart.  Convention: start the filename with ``_`` (e.g.
``ddtrace/internal/_my_ext.pyx``).

The build system passes the following Cython ``-E`` compile-time defines; use
them for version-guarded code:

.. code-block:: cython

    # Available as Cython compile-time constants:
    # PY_MAJOR_VERSION, PY_MINOR_VERSION, PY_MICRO_VERSION, PY_VERSION_HEX

**Step 2 — Register it in ``CMakeLists.txt``.**

Inside the ``if(DD_CYTHONIZE)`` block (search for ``"The N Cython extensions"``),
add a call to ``dd_add_cython_ext()``:

.. code-block:: cmake

    dd_add_cython_ext(cython_my_ext          # CMake target name (must be unique)
        "ddtrace/internal"                   # install destination (Python package path)
        PYX ddtrace/internal/_my_ext.pyx)   # path to .pyx, relative to repo root

If your extension needs extra compile definitions or link libraries:

.. code-block:: cmake

    dd_add_cython_ext(cython_my_ext
        "ddtrace/internal"
        PYX  ddtrace/internal/_my_ext.pyx
        DEFS MY_DEFINE=1                     # passed as -DMY_DEFINE=1
        LIBS some_lib)                       # linked with target_link_libraries

For include directories that are not automatically on the path, add them after
the helper call:

.. code-block:: cmake

    target_include_directories(cython_my_ext PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/ddtrace/internal")

**What the helper does:**

- Invokes ``cython --depfile`` at build time, generating
  ``build/cmake-{tag}/cython/ddtrace/internal/_my_ext.c`` and a ``.dep`` file
  that Ninja uses to track all transitively included ``.pxd`` files.
- Compiles the generated ``.c`` into a Python extension module via
  ``Python3_add_library()``.
- Names the output ``_my_ext<PYTHON_EXT_SUFFIX>`` (e.g.
  ``_my_ext.cpython-313-darwin.so``) and installs it to the destination.

**Step 3 — Import as usual.**

.. code-block:: python

    from ddtrace.internal._my_ext import my_function

**Step 4 — Rebuild.**

.. code-block:: bash

    pip install --no-build-isolation -e .


.. _c-cpp-extensions:

C and C++ Extensions
--------------------

For a new C or C++ Python extension module, use ``Python3_add_library()``
directly in ``CMakeLists.txt``.

Simple single-file extension
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: cmake

    Python3_add_library(my_ext MODULE ddtrace/internal/my_ext.cpp)

    target_compile_features(my_ext PRIVATE cxx_std_17)   # or cxx_std_20

    target_compile_options(my_ext PRIVATE
        -Wall -Wextra
        $<$<BOOL:${DD_FAST_BUILD}>:-O0>)   # honour DD_FAST_BUILD

    set_target_properties(my_ext PROPERTIES
        OUTPUT_NAME "my_ext"               # name Python will import
        PREFIX      ""
        SUFFIX      "${PYTHON_EXT_SUFFIX}")

    install(TARGETS my_ext
        LIBRARY DESTINATION "ddtrace/internal")

Multi-file extension
~~~~~~~~~~~~~~~~~~~~

Collect sources with ``file(GLOB ...)`` or list them explicitly:

.. code-block:: cmake

    set(_my_ext_dir "${CMAKE_CURRENT_SOURCE_DIR}/ddtrace/internal/my_ext")

    file(GLOB _my_ext_sources "${_my_ext_dir}/*.cpp")

    Python3_add_library(my_ext MODULE ${_my_ext_sources})

Extension that links against Abseil
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Abseil (``absl::*``) is pre-built into ``build/cmake-{tag}/absl-install/``
after the first build.  Link against it directly:

.. code-block:: cmake

    target_link_libraries(my_ext PRIVATE
        absl::base
        absl::flat_hash_map
        absl::strings)

Extension that uses Rust-generated C headers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``_native`` Rust crate (via libdatadog) generates C headers into
``${CMAKE_BINARY_DIR}/cargo/build/include/``, exposed as
``${Datadog_INCLUDE_DIRS}``:

.. code-block:: cmake

    target_include_directories(my_ext PRIVATE "${Datadog_INCLUDE_DIRS}")

    # Ensure the Rust crate is built before compiling this extension.
    add_dependencies(my_ext _native)

Extension that links ``dd_wrapper``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``dd_wrapper`` is a shared C++ library wrapping the Rust profiling API.
Extensions in ``ddtrace/internal/datadog/profiling/`` (such as ``_ddup`` and
``_stack``) link against it and use helpers from
``cmake/ProfExtensionHelpers.cmake`` to handle the boilerplate.

For a **new profiling extension** in that subdirectory:

.. code-block:: cmake

    list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")
    include(ProfExtensionHelpers)

    # Create an IMPORTED stub for dd_wrapper in standalone / test builds.
    # No-op when building inside the scikit-build-core tree (dd_wrapper already exists).
    dd_import_dd_wrapper()

    add_library(${EXTENSION_NAME} SHARED src/my_ext.cpp)
    target_link_libraries(${EXTENSION_NAME} PRIVATE dd_wrapper)

    # Strips lib prefix/suffix, sets RPATH, and registers the install rule.
    dd_finalize_profiling_extension(${EXTENSION_NAME})

``dd_finalize_profiling_extension()`` sets ``INSTALL_RPATH`` to ``"@loader_path/.."``
(macOS) / ``"$ORIGIN/.."`` (Linux), which resolves to the directory containing
``libdd_wrapper`` in both the wheel layout and editable installs.  For test
builds that set ``LIB_INSTALL_DIR`` the actual install path is appended
automatically.

For an extension **outside** the profiling subdirectory that links
``dd_wrapper``, set RPATH manually so the relative path from your install
destination to ``ddtrace/internal/datadog/profiling/`` is correct:

.. code-block:: cmake

    target_link_libraries(my_ext PRIVATE dd_wrapper)

    if(APPLE)
        set_target_properties(my_ext PROPERTIES
            INSTALL_RPATH "@loader_path/../../internal/datadog/profiling"
            BUILD_WITH_INSTALL_RPATH TRUE)
    elseif(UNIX)
        set_target_properties(my_ext PROPERTIES
            INSTALL_RPATH "$ORIGIN/../../internal/datadog/profiling"
            BUILD_WITH_INSTALL_RPATH TRUE)
    endif()

Adjust the relative path to match your extension's install destination.

Platform guards
~~~~~~~~~~~~~~~

Some extensions are Linux/macOS-only.  Wrap them in a platform check:

.. code-block:: cmake

    if(UNIX AND NOT WIN32 AND CMAKE_SIZEOF_VOID_P EQUAL 8)
        # 64-bit Linux and macOS only
        Python3_add_library(my_ext MODULE ...)
        ...
    endif()

Where to place the ``CMakeLists.txt`` block
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Simple extensions: add directly in the top-level ``CMakeLists.txt``, near the
  other C/C++ extension blocks (search for ``"Pure C/C++"``).
- Complex extensions with many sources or their own CMake logic: create
  ``ddtrace/your_package/CMakeLists.txt`` and add
  ``add_subdirectory(ddtrace/your_package)`` to the top-level file.  Export the
  ``EXTENSION_SUFFIX`` and ``Datadog_INCLUDE_DIRS`` variables are already set at
  the top level and visible in subdirectories.


.. _rust-extensions:

Rust Extensions
---------------

All Rust code lives in a single Cargo workspace crate at ``src/native/``.  The
crate is compiled into one cdylib (``_native<PYTHON_EXT_SUFFIX>``) and
installed to ``ddtrace/internal/native/``.  Adding new Rust functionality means
extending that crate, not creating a second crate.

Adding a new Rust module
~~~~~~~~~~~~~~~~~~~~~~~~

**Step 1 — Add Rust source files** under ``src/native/src/``.

**Step 2 — Expose a Python API with PyO3.**

The crate uses `PyO3 <https://pyo3.rs>`_ for Python bindings.  Register new
submodules in ``src/native/src/lib.rs``:

.. code-block:: rust

    // src/native/src/my_module.rs
    use pyo3::prelude::*;

    #[pyfunction]
    pub fn my_function(py: Python<'_>) -> PyResult<()> {
        // ...
        Ok(())
    }

    pub fn register(parent: &Bound<'_, PyModule>) -> PyResult<()> {
        let m = PyModule::new(parent.py(), "my_module")?;
        m.add_function(wrap_pyfunction!(my_function, &m)?)?;
        parent.add_submodule(&m)?;
        Ok(())
    }

    // In lib.rs, inside the root #[pymodule]:
    // my_module::register(m)?;

**Step 3 — Add a Cargo feature (optional).**

If the functionality should only be included on certain platforms, add a
feature in ``src/native/Cargo.toml``:

.. code-block:: toml

    [features]
    my_feature = ["some_dep"]

Then conditionally enable the feature in ``CMakeLists.txt`` alongside the
existing feature-selection logic (search for ``DD_RUST_FEATURES``):

.. code-block:: cmake

    if(some_condition)
        list(APPEND DD_RUST_FEATURES "my_feature")
    endif()

And gate the Rust code with a feature flag:

.. code-block:: rust

    #[cfg(feature = "my_feature")]
    pub mod my_module;

**Step 4 — Generating C headers for C/C++ consumers.**

If your Rust code exposes a C API that C/C++ extensions need to call, use
`cbindgen <https://github.com/mozilla/cbindgen>`_ in the crate's ``build.rs``
to generate a header.  The header will land in
``${CMAKE_BINARY_DIR}/cargo/build/include/`` (``${Datadog_INCLUDE_DIRS}``) and
is available to C/C++ targets via the pattern shown in
:ref:`c-cpp-extensions`.

After changing the Rust code, run ``dedup_headers`` if you added new types to
the generated headers (it removes duplicate type definitions across header
files).  The build does this automatically as a POST_BUILD step.

**Step 5 — Rebuild.**

.. code-block:: bash

    pip install --no-build-isolation -e .

Import the new module:

.. code-block:: python

    from ddtrace.internal.native import my_module
    my_module.my_function()

Minimum Rust version
~~~~~~~~~~~~~~~~~~~~

The minimum supported Rust version is pinned in ``src/native/Cargo.toml``
under ``[package] rust-version``.  Do not lower it without a discussion —
CI enforces this version.


Checklist for any new native extension
---------------------------------------

Before opening a PR:

- [ ] Read ``docs/native-code-review.md`` and ``docs/contributing.rst``.
- [ ] Verify the extension builds on a clean tree:
  ``rm -rf build/cmake-* && pip install --no-build-isolation -e .``
- [ ] Verify a no-op incremental rebuild is fast (< 10 s):
  run ``pip install --no-build-isolation -e .`` a second time.
- [ ] Add or update Python-level tests under ``tests/``.  Run them with the
  ``run-tests`` skill.
- [ ] Check for memory safety issues (GIL, PyObject lifetime, fork safety) —
  see the checklist in ``docs/native-code-review.md``.
- [ ] Ensure the extension is skipped gracefully on unsupported platforms
  (use platform guards in CMake; provide a pure-Python fallback if needed).
- [ ] Check that the install destination matches the Python import path.
- [ ] Run the linter (``lint`` skill) before committing.
