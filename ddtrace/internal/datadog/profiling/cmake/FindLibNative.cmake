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

if(DEFINED RUST_GENERATED_HEADERS_DIR)
    set(SOURCE_INCLUDE_DIR ${RUST_GENERATED_HEADERS_DIR})
else()
    # Fallback for standalone legacy builds that still use setup.py-style target directories.
    set(SOURCE_INCLUDE_DIR
        ${CMAKE_SOURCE_DIR}/../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include)
endif()

if(NOT EXISTS "${SOURCE_INCLUDE_DIR}")
    message(FATAL_ERROR "Rust-generated native headers not found at ${SOURCE_INCLUDE_DIR}")
endif()

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                         INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
