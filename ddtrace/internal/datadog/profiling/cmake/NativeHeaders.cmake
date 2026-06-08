# Resolves NATIVE_HEADERS_DIR — the absolute path to libdatadog's generated C headers (produced by the Rust crate under
# src/native/ and written to target<MAJOR>.<MINOR>/include).
#
# Primary source: setup.py passes -DRUST_GENERATED_HEADERS_DIR=<abs path> to every CMake invocation via
# _get_common_cmake_args. Whenever that variable is set, we trust it.
#
# Fallback: build_standalone.sh does NOT pass RUST_GENERATED_HEADERS_DIR, so we compute a path relative to this module's
# own location. Callers must have already invoked find_package(Python3) so that Python3_VERSION_MAJOR/_MINOR are
# defined; the fallback uses those to pick the right per-minor target directory (matching setup.py's CARGO_TARGET_DIR
# layout).
#
# Consumers must have "${CMAKE_CURRENT_SOURCE_DIR}/../cmake" on CMAKE_MODULE_PATH before calling include(NativeHeaders).

if(DEFINED RUST_GENERATED_HEADERS_DIR)
    set(NATIVE_HEADERS_DIR "${RUST_GENERATED_HEADERS_DIR}")
else()
    if(NOT DEFINED Python3_VERSION_MAJOR OR NOT DEFINED Python3_VERSION_MINOR)
        message(
            FATAL_ERROR
                "NativeHeaders: RUST_GENERATED_HEADERS_DIR is not set and Python3_VERSION_MAJOR/_MINOR are undefined. "
                "Call find_package(Python3) before include(NativeHeaders), or pass -DRUST_GENERATED_HEADERS_DIR "
                "(as setup.py does).")
    endif()
    get_filename_component(
        NATIVE_HEADERS_DIR
        "${CMAKE_CURRENT_LIST_DIR}/../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include"
        ABSOLUTE)
endif()
