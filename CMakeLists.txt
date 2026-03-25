cmake_minimum_required(VERSION 3.24)
project(ddtrace LANGUAGES C CXX)

# AIDEV-NOTE: This is the scikit-build-core entry point. Shared infrastructure is declared here; each extension module
# has its own CMakeLists.txt included via add_subdirectory().  The top-level is responsible only for components that
# must be shared across modules: - Build options and build-type mapping - Python discovery and PYTHON_EXT_SUFFIX -
# sccache (compiler launcher) - Abseil (shared by iast_native and _memalloc — fetch once, link many) - Corrosion / Rust
# _native extension + generated C headers - Cython infrastructure (find cython, version hex, dd_add_cython_ext helper)

# ── Options ───────────────────────────────────────────────────────────────────

# DD_COMPILE_MODE is injected by scikit-build-core from the DD_COMPILE_MODE env var (see pyproject.toml
# [tool.scikit-build.cmake.define]).  Map it to the canonical CMAKE_BUILD_TYPE so all CMake components see the same
# build type. We set it as a CACHE variable with FORCE so it overrides the default set below and any value
# scikit-build-core writes via --config.
if(DEFINED DD_COMPILE_MODE AND NOT DD_COMPILE_MODE STREQUAL "")
    set(CMAKE_BUILD_TYPE
        "${DD_COMPILE_MODE}"
        CACHE STRING "Build type (overridden by DD_COMPILE_MODE)" FORCE)
    message(STATUS "DD_COMPILE_MODE=${DD_COMPILE_MODE} → CMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}")
endif()

option(DD_SERVERLESS_BUILD "Build the serverless-only wheel flavor" OFF)
option(DD_FAST_BUILD "Trade optimisation for faster compilation (dev only)" OFF)
option(DD_USE_SCCACHE "Use sccache as a compiler/linker launcher" OFF)
option(DD_CYTHONIZE "Compile Cython .pyx files into C extensions" ON)

set(CMAKE_BUILD_TYPE
    "RelWithDebInfo"
    CACHE STRING "Build type: Debug | RelWithDebInfo | Release | MinSizeRel")

# ── Module path ─────────────────────────────────────────────────────────────── cmake/ holds project-wide helpers
# (CythonHelpers.cmake, RunDedupHeaders.cmake, etc.). The profiling cmake modules are also exposed globally so every
# add_subdirectory() can resolve AnalysisFunc, FindClangtidy, etc. without each sub-CMakeLists.txt needing its own
# relative-path MODULE_PATH entry.
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake"
     "${CMAKE_CURRENT_SOURCE_DIR}/ddtrace/internal/datadog/profiling/cmake")

# ── sccache ───────────────────────────────────────────────────────────────────
if(DD_USE_SCCACHE)
    find_program(SCCACHE_EXE sccache HINTS "$ENV{HOME}/.cargo/bin")
    if(SCCACHE_EXE)
        set(CMAKE_C_COMPILER_LAUNCHER "${SCCACHE_EXE}")
        set(CMAKE_CXX_COMPILER_LAUNCHER "${SCCACHE_EXE}")
        message(STATUS "sccache enabled: ${SCCACHE_EXE}")
    else()
        message(WARNING "DD_USE_SCCACHE requested but sccache not found in PATH")
    endif()
endif()

# ── Python ────────────────────────────────────────────────────────────────────
find_package(
    Python3
    COMPONENTS Interpreter Development.Module
    REQUIRED)

# Python extension suffix, e.g. ".cpython-311-x86_64-linux-gnu.so" or ".pyd". Used to name extension targets
# consistently with what Python expects.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'), end='')"
    OUTPUT_VARIABLE PYTHON_EXT_SUFFIX COMMAND_ERROR_IS_FATAL ANY)

# EXTENSION_SUFFIX is read by dd_wrapper/CMakeLists.txt to compose its output file name (e.g.
# "dd_wrapper.cpython-311-x86_64-linux-gnu.so").
set(EXTENSION_SUFFIX "${PYTHON_EXT_SUFFIX}")

# ── Abseil (shared dep: iast_native + _memalloc) ────────────────────────────── Fetched once here so every extension
# links against the same compiled copy.
#
# AIDEV-NOTE: Incremental-build optimisation. On the very first build we compile Abseil via FetchContent and immediately
# install it to ${CMAKE_BINARY_DIR}/absl-install — a stable path inside the persistent build tree
# (build/cmake-<wheel_tag>/).  On every subsequent cmake reconfigure (triggered by each `pip install -e .`) we detect
# that installed tree and satisfy the dependency via find_package(absl) instead of running FetchContent_MakeAvailable.
# This eliminates Abseil subproject re-configure overhead on incremental builds (~10-19 configure targets skipped). To
# force a fresh Abseil build (e.g. after bumping GIT_TAG), delete: build/cmake-<wheel_tag>/absl-install/
set(_absl_version "20250127.1")
set(_absl_install_dir "${CMAKE_BINARY_DIR}/absl-install")
set(_absl_marker "${_absl_install_dir}/.absl_version")
set(_absl_cmake_cfg "${_absl_install_dir}/lib/cmake/absl/abslConfig.cmake")

# Read the version we last installed (empty string if not yet installed).
set(_absl_installed_version "")
if(EXISTS "${_absl_marker}")
    file(READ "${_absl_marker}" _absl_installed_version)
    string(STRIP "${_absl_installed_version}" _absl_installed_version)
endif()

include(FetchContent)
set(FETCHCONTENT_UPDATES_DISCONNECTED
    ON
    CACHE BOOL "" FORCE)

if(EXISTS "${_absl_cmake_cfg}" AND "${_absl_installed_version}" STREQUAL "${_absl_version}")
    # Fast path: Abseil already pre-installed in the persistent build tree.
    message(STATUS "Abseil ${_absl_version}: using pre-installed copy at ${_absl_install_dir}")
    list(PREPEND CMAKE_PREFIX_PATH "${_absl_install_dir}")
    find_package(absl REQUIRED)
else()
    # Slow path (first build, or version bump): fetch, compile, then install.
    if(EXISTS "${_absl_install_dir}" AND NOT "${_absl_installed_version}" STREQUAL "${_absl_version}")
        message(
            STATUS "Abseil version changed (${_absl_installed_version} -> ${_absl_version}), clearing cached install")
        file(REMOVE_RECURSE "${_absl_install_dir}")
    endif()
    set(ABSL_ENABLE_INSTALL
        ON
        CACHE BOOL "" FORCE)
    set(ABSL_BUILD_TESTING
        OFF
        CACHE BOOL "" FORCE)
    set(BUILD_TESTING
        OFF
        CACHE BOOL "" FORCE)
    # Abseil static libs are linked into Python extension modules (.so/.pyd), which are shared objects — they require
    # -fPIC on all object files.
    set(CMAKE_POSITION_INDEPENDENT_CODE
        ON
        CACHE BOOL "" FORCE)
    FetchContent_Declare(
        absl
        GIT_REPOSITORY https://github.com/abseil/abseil-cpp.git
        GIT_TAG ${_absl_version}
        GIT_SHALLOW TRUE
        GIT_PROGRESS TRUE)
    FetchContent_MakeAvailable(absl)

    # After the first successful build, install Abseil to the stable location so the next cmake reconfigure can take the
    # fast find_package path above. cmake -E echo cannot redirect output, so generate a small cmake -P script at
    # configure time to write the version marker file at build time.
    set(_absl_write_marker "${CMAKE_BINARY_DIR}/write_absl_marker.cmake")
    file(WRITE "${_absl_write_marker}" "file(WRITE \"${_absl_marker}\" \"${_absl_version}\")\n")
    add_custom_target(
        absl_preinstall ALL
        COMMAND "${CMAKE_COMMAND}" --install "${absl_BINARY_DIR}" --prefix "${_absl_install_dir}" --config $<CONFIG>
        COMMAND "${CMAKE_COMMAND}" -P "${_absl_write_marker}"
        COMMENT "Installing Abseil ${_absl_version} to persistent build tree"
        VERBATIM)
    # Ensure ALL Abseil library targets are built before we run cmake --install, which registers install rules for every
    # Abseil library regardless of whether anything in our tree links against it.  Depending on only absl::base leaves
    # unlinked libraries (e.g. absl_scoped_set_env) unbuilt → install fails.
    #
    # ABSL_BUILD_TESTING=OFF is set above, so this only enumerates library targets. AIDEV-NOTE: When
    # ABSL_ENABLE_INSTALL=ON, Abseil names its CMake targets without the "absl_" prefix (e.g. "base" not "absl_base");
    # the absl:: namespace aliases point to these same targets.
    function(_absl_collect_targets _out_var _dir)
        get_property(
            _subdirs
            DIRECTORY "${_dir}"
            PROPERTY SUBDIRECTORIES)
        foreach(_sub IN LISTS _subdirs)
            _absl_collect_targets(${_out_var} "${_sub}")
        endforeach()
        get_property(
            _targets
            DIRECTORY "${_dir}"
            PROPERTY BUILDSYSTEM_TARGETS)
        set(${_out_var}
            ${${_out_var}} ${_targets}
            PARENT_SCOPE)
    endfunction()
    _absl_collect_targets(_absl_all_targets "${absl_SOURCE_DIR}")
    add_dependencies(absl_preinstall ${_absl_all_targets})
endif()

# ── Rust extension via Corrosion ──────────────────────────────────────────────

# Feature selection — mirrors the logic in setup.py: stats always; profiling + crashtracker on 64-bit Linux/macOS
# (unless serverless); ffe on all platforms (unless serverless).
set(DD_RUST_FEATURES "stats")
if((CMAKE_SYSTEM_NAME STREQUAL "Linux" OR CMAKE_SYSTEM_NAME STREQUAL "Darwin") AND CMAKE_SIZEOF_VOID_P EQUAL 8)
    list(APPEND DD_RUST_FEATURES "profiling")
    if(NOT DD_SERVERLESS_BUILD)
        list(APPEND DD_RUST_FEATURES "crashtracker")
    endif()
endif()
if(NOT DD_SERVERLESS_BUILD)
    list(APPEND DD_RUST_FEATURES "ffe")
endif()

FetchContent_Declare(
    Corrosion
    GIT_REPOSITORY https://github.com/corrosion-rs/corrosion.git
    GIT_TAG v0.5
    GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(Corrosion)

corrosion_import_crate(MANIFEST_PATH "${CMAKE_CURRENT_SOURCE_DIR}/src/native/Cargo.toml" CRATE_TYPES cdylib FEATURES
                       ${DD_RUST_FEATURES})

# AIDEV-NOTE: Corrosion v0.5 passes --target-dir to cargo directly, overriding CARGO_TARGET_DIR.  The actual Cargo
# output directory is therefore under the CMake binary tree: ${CMAKE_BINARY_DIR}/cargo/build/. We still set
# CARGO_TARGET_DIR (used by build.rs scripts that read it directly) to the same Corrosion-controlled path, plus
# PYO3_PYTHON for pyo3-build-config.
set(_corrosion_cargo_build_dir "${CMAKE_BINARY_DIR}/cargo/build")

corrosion_set_env_vars(
    _native
    # Sync our env var with the path Corrosion actually passes via --target-dir.
    "CARGO_TARGET_DIR=${_corrosion_cargo_build_dir}"
    # Point pyo3-build-config at the same Python as our cmake build.
    "PYO3_PYTHON=${Python3_EXECUTABLE}")

if(DD_FAST_BUILD)
    corrosion_set_env_vars(_native "CARGO_PROFILE_RELEASE_LTO=off" "CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16"
                           "CARGO_PROFILE_RELEASE_OPT_LEVEL=2")
endif()

if(DD_USE_SCCACHE AND SCCACHE_EXE)
    corrosion_set_env_vars(_native "RUSTC_WRAPPER=${SCCACHE_EXE}")
endif()

# ── Rust-generated C headers ────────────────────────────────────────────────── libdatadog's build-common crate writes
# headers to CARGO_TARGET_DIR/include/. Since Corrosion controls the target dir, headers land in:
# ${CMAKE_BINARY_DIR}/cargo/build/include/ Ensure the directory exists at configure time so target_include_directories
# on downstream targets doesn't warn about missing paths on a clean checkout. The actual header files appear after the
# first Rust build step.
set(_native_headers_dir "${_corrosion_cargo_build_dir}/include")
file(MAKE_DIRECTORY "${_native_headers_dir}")

# Propagate for dd_wrapper, _ddup, _stack, _memalloc which include Datadog_INCLUDE_DIRS in their
# target_include_directories calls.
set(Datadog_INCLUDE_DIRS "${_native_headers_dir}")

# ── Rust extension: Python-named output + install rule ──────────────────────── Cargo produces lib_native.so (Unix lib
# prefix); Python expects _native<suffix>.
set(_native_python_name "_native${PYTHON_EXT_SUFFIX}")
set(_native_python_path "${CMAKE_CURRENT_BINARY_DIR}/${_native_python_name}")

# AIDEV-NOTE: In Corrosion v0.5 the target created by corrosion_import_crate() is an INTERFACE library —
# $<TARGET_FILE:_native> and POST_BUILD on it both fail.  Instead: 1. "cargo-build__native" (double underscore) is the
# real build target and accepts POST_BUILD steps. 2. We compute the Cargo artifact path directly from Corrosion's build
# dir, the Rust target triple (Rust_CARGO_TARGET_CACHED set by Corrosion/FindRust), and the build type → Cargo profile
# mapping.  No generator expressions needed.
#
# Cargo profile directory: Debug    → debug/ anything else (Release, RelWithDebInfo, MinSizeRel) → release/
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(_cargo_profile_dir "debug")
else()
    set(_cargo_profile_dir "release")
endif()

# Platform-specific Cargo output filename (lib prefix on Unix, no prefix on Windows)
if(APPLE)
    set(_cargo_lib_name "lib_native.dylib")
elseif(WIN32)
    set(_cargo_lib_name "_native.dll")
else()
    set(_cargo_lib_name "lib_native.so")
endif()

# Corrosion always passes --target=<triple> so the output is under a triple subdir. Rust_CARGO_TARGET is a non-cache
# variable set inside Corrosion's FetchContent scope; Rust_CARGO_TARGET_CACHED is the CACHE INTERNAL form that's visible
# at our scope.
set(_cargo_artifact
    "${_corrosion_cargo_build_dir}/${Rust_CARGO_TARGET_CACHED}/${_cargo_profile_dir}/${_cargo_lib_name}")

add_custom_command(
    TARGET cargo-build__native
    POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy "${_cargo_artifact}" "${_native_python_path}"
    COMMENT "Renaming Rust extension to Python ABI name: ${_native_python_name}")

install(FILES "${_native_python_path}" DESTINATION "ddtrace/internal/native")

# ── dedup_headers (post-processes Rust-generated C headers) ─────────────────── AIDEV-NOTE: dedup_headers is a
# libdatadog tool that merges duplicate type definitions across the generated headers so downstream C/C++ code can
# include them all without multiple-definition errors.  Install with: cargo install --git
# https://github.com/DataDog/libdatadog tools --bin dedup_headers
if("profiling" IN_LIST DD_RUST_FEATURES)
    find_program(DEDUP_HEADERS_EXE dedup_headers HINTS "$ENV{HOME}/.cargo/bin")
    if(DEDUP_HEADERS_EXE)
        add_custom_command(
            TARGET cargo-build__native
            POST_BUILD
            COMMAND
                "${CMAKE_COMMAND}" "-DHEADERS_DIR=${_native_headers_dir}/datadog"
                "-DDEDUP_HEADERS_EXE=${DEDUP_HEADERS_EXE}" "-DNATIVE_LIB=${_cargo_artifact}" -P
                "${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunDedupHeaders.cmake"
            COMMENT "Running dedup_headers on Rust-generated C headers")
    else()
        message(STATUS "dedup_headers not found — skipping header deduplication.\n"
                       "Install with: cargo install --git https://github.com/DataDog/libdatadog"
                       " tools --bin dedup_headers")
    endif()
endif()

# ── Cython infrastructure ───────────────────────────────────────────────────── The dd_add_cython_ext() helper function
# is defined in cmake/CythonHelpers.cmake and is available in all subdirectories after this include().
include(CythonHelpers)

if(DD_CYTHONIZE)
    find_program(
        CYTHON_EXECUTABLE
        NAMES cython cython3
        HINTS "${Python3_ROOT_DIR}/bin" "${Python3_EXECUTABLE}/../..")

    if(NOT CYTHON_EXECUTABLE)
        message(FATAL_ERROR "cython executable not found. Install it with: pip install cython\n"
                            "Or disable Cython compilation with -DDD_CYTHONIZE=OFF.")
    endif()

    message(STATUS "Cython: ${CYTHON_EXECUTABLE}")

    # PY_VERSION_HEX mirrors Python's sys.hexversion: (major << 24) | (minor << 16) | (micro << 8) | 0xf0  (final
    # release)
    math(
        EXPR
        _py_version_hex
        "(${Python3_VERSION_MAJOR} * 16777216) \
       + (${Python3_VERSION_MINOR} *    65536) \
       + (${Python3_VERSION_PATCH} *      256) \
       + 240") # 0xf0 = final release serial
endif()

# ── Extension modules ───────────────────────────────────────────────────────── Each subdirectory owns its own targets;
# shared variables set above are inherited automatically by CMake's subdirectory scoping rules.

# ddtrace.internal: _threads (non-Win) + Cython: _tagset, _encoding, metrics_namespaces
add_subdirectory(ddtrace/internal)

# ddtrace.appsec._iast: _stacktrace, iastpatch, iast_native (non-Win)
add_subdirectory(ddtrace/appsec/_iast)

# Profiling extensions: dd_wrapper, _ddup, _stack (Linux/macOS 64-bit only). These are added before ddtrace/profiling so
# that dd_wrapper is a real CMake target by the time _memalloc links against it.
if(UNIX AND CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(LIB_INSTALL_DIR "ddtrace/internal/datadog/profiling")
    add_subdirectory(ddtrace/internal/datadog/profiling/dd_wrapper)

    set(EXTENSION_NAME
        "_ddup${PYTHON_EXT_SUFFIX}"
        CACHE STRING "ddup extension filename" FORCE)
    set(LIB_INSTALL_DIR "ddtrace/internal/datadog/profiling/ddup")
    add_subdirectory(ddtrace/internal/datadog/profiling/ddup)

    set(EXTENSION_NAME
        "_stack${PYTHON_EXT_SUFFIX}"
        CACHE STRING "stack extension filename" FORCE)
    set(LIB_INSTALL_DIR "ddtrace/internal/datadog/profiling/stack")
    add_subdirectory(ddtrace/internal/datadog/profiling/stack)
endif()

# ddtrace.profiling: _memalloc (Unix 64-bit) + Cython: _threading, _task, etc.
add_subdirectory(ddtrace/profiling)

# ddtrace.appsec._ddwaf: libddwaf pre-built shared library download + install
add_subdirectory(ddtrace/appsec/_ddwaf)

# psutil vendor C extensions (skipped on Pyston).
if(NOT DEFINED ENV{PYSTON_VERSION})
    add_subdirectory(ddtrace/vendor/psutil)
endif()
