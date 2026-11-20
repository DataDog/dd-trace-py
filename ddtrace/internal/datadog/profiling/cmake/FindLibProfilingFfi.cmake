find_package(Python3)

# The profiling FFI library is libdatadog's `ddog_prof_*` C API. It is built from src/profiling_ffi and staged by
# setup.py as `_profiling_ffi<EXTENSION_SUFFIX>` next to libdd_wrapper. It is separate from the tracer's native
# extension (`_ddtrace_native._native`, from the ddtrace-native package), which does not include the profiling FFI.
if(DEFINED PROFILING_FFI_LOCATION)
    set(SOURCE_LIB_DIR ${PROFILING_FFI_LOCATION})
else()
    message(
        FATAL_ERROR
            "PROFILING_FFI_LOCATION is not set. Use `python setup.py` or ddtrace/internal/datadog/profiling/build_standalone.sh "
            "to build profiling native extensions.")
endif()

if(DEFINED EXTENSION_SUFFIX)
    set(LIBRARY_NAME _profiling_ffi${EXTENSION_SUFFIX})
else()
    message(
        FATAL_ERROR
            "EXTENSION_SUFFIX is not set. Use `python setup.py` or ddtrace/internal/datadog/profiling/build_standalone.sh "
            "to build profiling native extensions.")
endif()

message(WARNING "SOURCE_LIB_DIR: ${SOURCE_LIB_DIR}")
message(WARNING "LIBRARY_NAME: ${LIBRARY_NAME}")

# We expect the profiling FFI library to be built and to have installed the headers in the following directory. It is
# configured in setup.py by setting CARGO_TARGET_DIR environment variable.
set(SOURCE_INCLUDE_DIR
    ${CMAKE_SOURCE_DIR}/../../../../../src/profiling_ffi/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include
)

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_profiling_ffi SHARED IMPORTED GLOBAL)
set_target_properties(_profiling_ffi PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                                INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
