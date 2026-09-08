find_package(Python3)

if(DEFINED NATIVE_EXTENSION_LOCATION)
    set(SOURCE_LIB_DIR ${NATIVE_EXTENSION_LOCATION})
else()
    message(
        FATAL_ERROR
            "NATIVE_EXTENSION_LOCATION is not set. Use `python setup.py` or ddtrace/internal/datadog/profiling/build_standalone.sh "
            "to build profiling native extensions.")
endif()

if(DEFINED EXTENSION_SUFFIX)
    set(LIBRARY_NAME _native${EXTENSION_SUFFIX})
else()
    message(
        FATAL_ERROR
            "EXTENSION_SUFFIX is not set. Use `python setup.py` or ddtrace/internal/datadog/profiling/build_standalone.sh "
            "to build profiling native extensions.")
endif()

message(WARNING "SOURCE_LIB_DIR: ${SOURCE_LIB_DIR}")
message(WARNING "LIBRARY_NAME: ${LIBRARY_NAME}")

# We expect the native extension to be built and installed the headers in the following directory. It is configured in
# setup.py by setting CARGO_TARGET_DIR environment variable.
if(DEFINED RUST_GENERATED_HEADERS_DIR)
    set(SOURCE_INCLUDE_DIR ${RUST_GENERATED_HEADERS_DIR})
else()
    set(SOURCE_INCLUDE_DIR
        ${CMAKE_SOURCE_DIR}/../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include)
endif()

get_filename_component(SOURCE_NATIVE_TARGET_DIR "${SOURCE_INCLUDE_DIR}/.." ABSOLUTE)
set(SOURCE_CXXBRIDGE_DIR ${SOURCE_NATIVE_TARGET_DIR}/cxxbridge)

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)
set(DEST_CXXBRIDGE_DIR ${DEST_LIB_DIR}/cxxbridge)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})
if(EXISTS ${SOURCE_CXXBRIDGE_DIR})
    file(COPY ${SOURCE_CXXBRIDGE_DIR} DESTINATION ${DEST_LIB_DIR})
endif()

set(LIBDD_PROFILING_CXX_SOURCE ${DEST_CXXBRIDGE_DIR}/sources/libdd-profiling/src/cxx.rs.cc)

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                         INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
