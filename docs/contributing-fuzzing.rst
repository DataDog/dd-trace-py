.. _fuzzing_guidelines:

Fuzzing Native Code
===================

This document describes how to add fuzzing harnesses for native C/C++ code in dd-trace-py.

What is Fuzzing?
----------------

Fuzzing is an automated testing technique that feeds random or mutated inputs to code to discover
bugs, crashes, and security vulnerabilities. For native C/C++ code, fuzzing can detect:

* Buffer overflows and memory corruption
* Use-after-free bugs
* Integer overflows
* Null pointer dereferences
* Undefined behavior

dd-trace-py uses **libFuzzer** with **AddressSanitizer (ASAN)** and **UndefinedBehaviorSanitizer (UBSAN)**
to continuously test native code components.

Fuzzing Infrastructure Overview
--------------------------------

The repository has a "0 click onboarding" fuzzing infrastructure that automatically discovers,
builds, uploads, and runs fuzzing harnesses.

**How it works:**

1. **Discovery**: CI recursively searches for ``**/fuzz/build.sh`` files anywhere in the repository
2. **Build**: Each discovered ``build.sh`` script is executed to compile fuzzing binaries
3. **Registration**: Built binaries are uploaded to Datadog's internal fuzzing platform
4. **Continuous Fuzzing**: Binaries run continuously with crash reporting to Slack (``#fuzzing-ops``)

**Architecture**::

    Repository
    └── <any-directory>/
        └── fuzz/
            ├── build.sh              # Build script (auto-discovered)
            ├── fuzz_*.cpp            # Fuzzing harness
            └── CMakeLists.txt        # Build configuration

    CI Pipeline (.gitlab/fuzz.yml):
    1. Discover: glob.glob("**/fuzz/build.sh")
    2. Build: Execute each build.sh
    3. Collect: Read /tmp/fuzz/build/fuzz_binaries.txt
    4. Upload: POST binaries to fuzzing API
    5. Register: Create continuous fuzzer
    6. Report: Crashes sent to Slack

Adding a New Fuzzing Harness
-----------------------------

1. Create Directory Structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a ``fuzz/`` subdirectory in your component:

.. code-block:: bash

    $ mkdir -p path/to/your/component/fuzz/
    $ cd path/to/your/component/fuzz/

2. Write Your Fuzzing Harness
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a C/C++ file implementing the libFuzzer interface:

.. code-block:: cpp

    // fuzz_your_component.cpp
    #include <cstddef>
    #include <cstdint>
    #include "your_component.h"  // Your code to test

    extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
        if (size == 0) {
            return 0;
        }

        // Call your code with fuzzer-generated input
        your_function_to_test(data, size);

        return 0;  // Continue fuzzing
    }

**Key points:**

* Implement ``LLVMFuzzerTestOneInput(const uint8_t* data, size_t size)``
* Return 0 to continue fuzzing
* Keep the harness simple - let sanitizers catch bugs
* Handle empty inputs gracefully

3. Create CMakeLists.txt
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: cmake

    cmake_minimum_required(VERSION 3.19)

    add_executable(fuzz_your_component
        fuzz_your_component.cpp
        ../src/your_source.c
    )

    target_include_directories(fuzz_your_component PRIVATE ../include)

    if(STACK_USE_LIBFUZZER)
        target_compile_options(fuzz_your_component PRIVATE
            -fsanitize=fuzzer,address,undefined
            -fno-omit-frame-pointer
        )
        target_link_options(fuzz_your_component PRIVATE
            -fsanitize=fuzzer,address,undefined
        )
    endif()

4. Create build.sh Script
~~~~~~~~~~~~~~~~~~~~~~~~~~

Create an executable ``build.sh``:

.. code-block:: bash

    #!/bin/bash
    set -e

    TARGET=fuzz_your_component
    BUILD_DIR=/tmp/fuzz/build/your_component  # Use unique subdirectory
    MANIFEST_FILE=/tmp/fuzz/build/fuzz_binaries.txt

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

    cmake -S "${SCRIPT_DIR}" -B "${BUILD_DIR}" \
          -DSTACK_USE_LIBFUZZER=ON \
          -DCMAKE_C_COMPILER=clang \
          -DCMAKE_CXX_COMPILER=clang++ \
          -DCMAKE_BUILD_TYPE=RelWithDebInfo \
          -DCMAKE_C_FLAGS="-O1 -g -fsanitize=address,undefined" \
          -DCMAKE_CXX_FLAGS="-O1 -g -fsanitize=address,undefined" \
      && cmake --build "${BUILD_DIR}" -j --target $TARGET

    # Register binary in manifest (REQUIRED)
    BINARY_PATH="${BUILD_DIR}/${TARGET}"
    if [ -x "${BINARY_PATH}" ]; then
        echo "${BINARY_PATH}" >> "${MANIFEST_FILE}"
        echo "✅ Registered binary: ${BINARY_PATH}"
    else
        echo "❌ Binary not found: ${BINARY_PATH}"
        exit 1
    fi

**Make executable:** ``chmod +x build.sh``

**Critical requirements:**

* Script must be named exactly ``build.sh``
* Must append binary path to ``/tmp/fuzz/build/fuzz_binaries.txt``
* Use a unique ``BUILD_DIR`` subdirectory
* Exit with non-zero status if build fails

5. Test Locally
~~~~~~~~~~~~~~~~

**Using Docker** (recommended):

.. code-block:: bash

    $ docker build -f docker/Dockerfile.fuzz -t ddtrace-py-fuzz .
    $ docker run --rm -it ddtrace-py-fuzz

**Local build:**

.. code-block:: bash

    $ cd path/to/your/component/fuzz/
    $ ./build.sh
    $ /tmp/fuzz/build/your_component/fuzz_your_component -max_total_time=60

6. Commit and Push
~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    $ git add path/to/your/component/fuzz/
    $ git commit -m "feat: add fuzzing for your component"
    $ git push

7. Trigger Fuzzing Job in CI
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The fuzzing job runs automatically on the ``main`` branch but must be triggered manually on pull requests.

**In your GitLab CI pipeline:**

1. Go to your merge request's **Pipelines** tab
2. Find the ``fuzz_infra`` job (it will show as "manual" or have a play button)
3. Click the play button (▶) to trigger the job

This builds your fuzzer, uploads it to the fuzzing platform, and verifies it works correctly.

**After merging to main**, the fuzzer runs automatically on every commit and continuously in the background.

Example: Existing Fuzzer
-------------------------

See the profiling stack sampler fuzzer for a complete example:

.. code-block:: text

    ddtrace/internal/datadog/profiling/stack/fuzz/
    ├── build.sh
    ├── fuzz_echion_remote_read.cpp
    └── CMakeLists.txt

This fuzzer tests echion's ability to parse Python stack frames from remote processes.

Advanced: Testing Remote Process Memory Reads
----------------------------------------------

For code that reads memory from remote processes (like echion), use conditional compilation
to replace the real memory read function with a mock:

.. code-block:: cpp

    // In your header file (e.g., vm.h)
    #if defined(YOUR_COMPONENT_FUZZING)
    extern "C" int your_fuzz_copy_memory(pid_t pid, void* addr,
                                         size_t len, void* buf);
    #define copy_memory your_fuzz_copy_memory
    #else
    int copy_memory(pid_t pid, void* addr, size_t len, void* buf);
    #endif

    // In your fuzzer harness
    static thread_local const uint8_t* g_data = nullptr;
    static thread_local size_t g_size = 0;

    extern "C" int your_fuzz_copy_memory(pid_t pid, void* addr,
                                         size_t len, void* buf) {
        // Serve fuzzer input bytes as "fake remote memory"
        // ... bounds checking ...
        memcpy(buf, g_data + offset, len);
        return 0;
    }

See ``ddtrace/internal/datadog/profiling/stack/fuzz/fuzz_echion_remote_read.cpp`` for a complete example.

Common Build Options
--------------------

**Compiler flags:**

``-O1``
  Light optimization for reasonable performance while preserving debuggability

``-g``
  Include debug symbols for better crash reports

``-fno-omit-frame-pointer``
  Required for accurate ASAN stack traces

``-fsanitize=fuzzer``
  Enable libFuzzer instrumentation

``-fsanitize=address``
  Enable AddressSanitizer for memory error detection

``-fsanitize=undefined``
  Enable UndefinedBehaviorSanitizer

**libFuzzer runtime options:**

.. code-block:: bash

    $ ./fuzzer corpus/ -max_total_time=60 -max_len=4096 -jobs=4

``-max_total_time=N``
  Run for N seconds then exit

``-max_len=N``
  Limit input size to N bytes

``-jobs=N``
  Run N parallel fuzzing jobs

``-artifact_prefix=path/``
  Store crash artifacts in this directory

Current Limitations
-------------------

**Single Python Version**
  Fuzzing currently only runs on Python 3.12.3, despite dd-trace-py supporting Python 3.9-3.14.
  Bugs in version-specific code paths may not be discovered.

Resources and References
------------------------

**libFuzzer documentation:**
  https://llvm.org/docs/LibFuzzer.html

**AddressSanitizer:**
  https://clang.llvm.org/docs/AddressSanitizer.html

**UndefinedBehaviorSanitizer:**
  https://clang.llvm.org/docs/UndefinedBehaviorSanitizer.html

**Fuzzing best practices:**
  https://github.com/google/fuzzing/blob/master/docs/good-fuzz-target.md

**Example fuzzer in this repository:**
  ``ddtrace/internal/datadog/profiling/stack/fuzz/fuzz_echion_remote_read.cpp``

**Crash reports:**
  Check ``#fuzzing-ops`` Slack channel

Quick Start Checklist
---------------------

1. ☐ Create ``fuzz/`` directory in your component
2. ☐ Write ``fuzz_*.cpp`` implementing ``LLVMFuzzerTestOneInput()``
3. ☐ Create ``CMakeLists.txt`` with fuzzer build configuration
4. ☐ Create executable ``build.sh`` that builds and registers binary
5. ☐ Test locally with Docker or manual build
6. ☐ Commit and push
7. ☐ Manually trigger ``fuzz_infra`` job in GitLab CI (on pull requests)
8. ☐ Monitor ``#fuzzing-ops`` for crash reports
