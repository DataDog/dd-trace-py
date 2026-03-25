find_package(Python3)

if(DEFINED NATIVE_EXTENSION_LOCATION)
    set(SOURCE_LIB_DIR ${NATIVE_EXTENSION_LOCATION})
else()
    message(FATAL_ERROR "NATIVE_EXTENSION_LOCATION is not set. Use `pip install --no-build-isolation -e .` or "
                        "ddtrace/internal/datadog/profiling/build_standalone.sh to build profiling native extensions.")
endif()

if(DEFINED EXTENSION_SUFFIX)
    set(LIBRARY_NAME _native${EXTENSION_SUFFIX})
else()
    message(FATAL_ERROR "EXTENSION_SUFFIX is not set. Use `pip install --no-build-isolation -e .` or "
                        "ddtrace/internal/datadog/profiling/build_standalone.sh to build profiling native extensions.")
endif()

message(WARNING "SOURCE_LIB_DIR: ${SOURCE_LIB_DIR}")
message(WARNING "LIBRARY_NAME: ${LIBRARY_NAME}")

# Locate the directory that contains the Rust-generated C headers (datadog/profiling.h, etc.).  The location varies by
# build system:
#
# scikit-build-core + Corrosion (new): build/cmake-<wheel_tag>/cargo/build/include/ Corrosion passes --target-dir to
# cargo, so CARGO_TARGET_DIR in env is set by build_backend.py / corrosion_set_env_vars to the same path.
#
# build_standalone.sh: The script sets CARGO_TARGET_DIR before invoking cargo build, so the headers end up at
# $CARGO_TARGET_DIR/include/.
#
# Legacy setup.py (for reference, no longer used): src/native/target<major>.<minor>/include/
#
# Resolution order: 1. CARGO_TARGET_DIR env var (covers both scikit-build-core and standalone). 2. Legacy path
# (src/native/target<major>.<minor>/include) for clean rooms that still have it from a prior build. 3. Glob for the
# scikit-build-core cmake binary tree (fallback if CARGO_TARGET_DIR was not exported by the caller).

set(_native_include_dir "")

# Step 1: CARGO_TARGET_DIR env var (most reliable — set by both new and standalone builds)
if(DEFINED ENV{CARGO_TARGET_DIR})
    set(_native_include_dir "$ENV{CARGO_TARGET_DIR}/include")
    message(STATUS "FindLibNative: using CARGO_TARGET_DIR → ${_native_include_dir}")
endif()

# Step 2: Legacy path (src/native/target<major>.<minor>/include)
if(NOT _native_include_dir OR NOT EXISTS "${_native_include_dir}")
    set(_native_include_legacy "${CMAKE_SOURCE_DIR}/../../../../../src/native/target\
${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include")
    if(EXISTS "${_native_include_legacy}")
        set(_native_include_dir "${_native_include_legacy}")
        message(STATUS "FindLibNative: using legacy target dir → ${_native_include_dir}")
    endif()
endif()

# Step 3: Glob for the scikit-build-core / Corrosion cmake binary tree
if(NOT _native_include_dir OR NOT EXISTS "${_native_include_dir}")
    file(GLOB _skb_cargo_includes "${CMAKE_SOURCE_DIR}/../../../../../build/cmake-*/cargo/build/include")
    if(_skb_cargo_includes)
        list(GET _skb_cargo_includes 0 _native_include_dir)
        message(STATUS "FindLibNative: found scikit-build-core cargo dir → ${_native_include_dir}")
    endif()
endif()

if(NOT _native_include_dir OR NOT EXISTS "${_native_include_dir}")
    message(
        FATAL_ERROR
            "Cannot locate Rust-generated headers (datadog/profiling.h).\n"
            "Run `pip install --no-build-isolation -e .` first, or run "
            "build_standalone.sh which sets CARGO_TARGET_DIR automatically.")
endif()

set(SOURCE_INCLUDE_DIR "${_native_include_dir}")

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                         INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
