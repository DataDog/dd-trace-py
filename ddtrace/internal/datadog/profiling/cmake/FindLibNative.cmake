find_package(Python3)

if(DEFINED NATIVE_EXTENSION_LOCATION)
    set(LIB_FILE_LOCATION ${NATIVE_EXTENSION_LOCATION})
else()
    message(
        FATAL_ERROR
            "NATIVE_EXTENSION_LOCATION is not set. Use `python setup.py` or ddtrace/internal/datadog/profiling/build_standalone.sh"
            "to build profiling native extensions.")
endif()

message(WARNING "LIB_FILE_LOCATION: ${LIB_FILE_LOCATION}")

# We expect the native extension to be built and installed the headers in the following directory. It is configured in
# setup.py by setting CARGO_TARGET_DIR environment variable.
set(SOURCE_INCLUDE_DIR ${CMAKE_SOURCE_DIR}/../../../../../src/native/target/include)

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILE "${LIB_FILE_LOCATION}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILE}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${LIB_FILE} INTERFACE_INCLUDE_DIRECTORIES
                                                                       ${DEST_INCLUDE_DIR})
