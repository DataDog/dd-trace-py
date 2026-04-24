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

# Resolves NATIVE_HEADERS_DIR (libdatadog's generated C headers for the active Python minor). See NativeHeaders.cmake
# for the resolution policy.
include(NativeHeaders)
set(SOURCE_INCLUDE_DIR "${NATIVE_HEADERS_DIR}")

set(DEST_LIB_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DEST_INCLUDE_DIR ${DEST_LIB_DIR}/include)

file(COPY ${SOURCE_INCLUDE_DIR} DESTINATION ${DEST_LIB_DIR})

file(GLOB LIB_FILES "${SOURCE_LIB_DIR}/${LIBRARY_NAME}")

message(WARNING "LIB_FILES LOCATION: ${LIB_FILES}")

add_library(_native SHARED IMPORTED GLOBAL)
set_target_properties(_native PROPERTIES IMPORTED_LOCATION ${SOURCE_LIB_DIR}/${LIBRARY_NAME}
                                         INTERFACE_INCLUDE_DIRECTORIES ${DEST_INCLUDE_DIR})
