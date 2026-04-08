cmake_minimum_required(VERSION 3.19)
include(CheckIPOSupported)

project(_memalloc)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Add compile options
add_compile_options(-fPIC -fvisibility=hidden -pthread -Wall -Wextra)

# Platform-specific compile definitions
if(APPLE)
    # Fix for newer macOS SDKs that don't define BSD-style types These are needed by Abseil on macOS
    add_compile_definitions(_DARWIN_C_SOURCE)
endif()

add_compile_definitions(_POSIX_C_SOURCE=200809L)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../../../cmake")
include(AbseilDep)

# Find Python (be flexible about what's available in build environments)
find_package(Python3 COMPONENTS Interpreter Development)

# Make sure we have necessary Python variables
if(NOT Python3_INCLUDE_DIRS)
    # Fallback to PYTHON_INCLUDE_DIRS if Python3_INCLUDE_DIRS not found
    if(PYTHON_INCLUDE_DIRS)
        set(Python3_INCLUDE_DIRS ${PYTHON_INCLUDE_DIRS})
    else()
        message(FATAL_ERROR "Python3_INCLUDE_DIRS not found")
    endif()
endif()

# Python3::Python target might not exist in all build environments so we'll link using include dirs and let the linker
# find Python dynamically
if(NOT TARGET Python3::Python)
    message(STATUS "Python3::Python target not found, using include dirs only")
endif()

# Source files for the extension
set(SOURCE_FILES _memalloc.cpp _memalloc_tb.cpp _memalloc_heap.cpp _memalloc_reentrant.cpp)

# Get the extension name from setup.py or use default Note: EXTENSION_NAME from setup.py already includes the full
# suffix
if(DEFINED EXTENSION_NAME)
    set(FULL_EXTENSION_NAME "${EXTENSION_NAME}")
else()
    set(FULL_EXTENSION_NAME "_memalloc.so")
endif()

# Create the shared library with the full name
add_library(${FULL_EXTENSION_NAME} SHARED ${SOURCE_FILES})

# Enable LTO (Link Time Optimization) if the compiler supports it
check_ipo_supported(RESULT ipo_supported)
if(ipo_supported)
    set_property(TARGET ${FULL_EXTENSION_NAME} PROPERTY INTERPROCEDURAL_OPTIMIZATION TRUE)
endif()

# Set properties to prevent CMake from adding any prefix or suffix
set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES PREFIX "" SUFFIX "")

# Set output directory if specified
if(DEFINED LIB_INSTALL_DIR)
    set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES LIBRARY_OUTPUT_DIRECTORY ${LIB_INSTALL_DIR})
endif()

# Include directories
target_include_directories(
    ${FULL_EXTENSION_NAME}
    PRIVATE ${Python3_INCLUDE_DIRS}
            ${CMAKE_CURRENT_SOURCE_DIR}
            ${CMAKE_CURRENT_SOURCE_DIR}/../../internal/datadog/profiling
            ${CMAKE_CURRENT_SOURCE_DIR}/../../internal/datadog/profiling/dd_wrapper/include
            ${RUST_GENERATED_HEADERS_DIR})

# Link libraries Python3::Python target might not exist in all build environments (e.g., manylinux) Python modules
# should use -undefined dynamic_lookup on macOS and not link to libpython on Linux
if(TARGET Python3::Python AND NOT APPLE)
    target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE Python3::Python)
endif()

# Link Abseil if available
if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug"
        OR (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}"
                                                                                          STREQUAL "false"))))
    target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE absl::flat_hash_map)
endif()

# Platform-specific settings
if(APPLE)
    # macOS specific - set rpath for libdd_wrapper and use dynamic lookup for Python symbols
    set_target_properties(
        ${FULL_EXTENSION_NAME}
        PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                   INSTALL_RPATH "@loader_path/../../internal/datadog/profiling"
                   LINK_FLAGS "-undefined dynamic_lookup")
elseif(UNIX)
    # Linux specific
    set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                                                            INSTALL_RPATH "$ORIGIN/../../internal/datadog/profiling")
endif()

# Link with libdd_wrapper
if(DEFINED DD_WRAPPER_DIR)
    set(_dd_wrapper_search_paths ${DD_WRAPPER_DIR})
    find_library(
        DD_WRAPPER_LIB
        NAMES libdd_wrapper${EXTENSION_SUFFIX}
        PATHS ${_dd_wrapper_search_paths}
        NO_DEFAULT_PATH)

    if(DD_WRAPPER_LIB)
        message(STATUS "Found libdd_wrapper: ${DD_WRAPPER_LIB}")
        target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE ${DD_WRAPPER_LIB})
    else()
        message(FATAL_ERROR "libdd_wrapper not found in ${_dd_wrapper_search_paths}")
    endif()
else()
    message(FATAL_ERROR "DD_WRAPPER_DIR is not set")
endif()

# Enable allocator-hook reentry assertions in test builds. setup.py turns this on when
# DD_PROFILING_MEMALLOC_ASSERT_ON_REENTRY is set in the build environment.
if(MEMALLOC_ASSERT_ON_REENTRY)
    message(STATUS "MEMALLOC_ASSERT_ON_REENTRY enabled: will abort on reentrant allocator hook calls")
    target_compile_definitions(${FULL_EXTENSION_NAME} PRIVATE MEMALLOC_ASSERT_ON_REENTRY)
endif()

# Add NDEBUG flag for release builds
if(CMAKE_BUILD_TYPE STREQUAL "Release"
   OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo"
   OR CMAKE_BUILD_TYPE STREQUAL "MinSizeRel")
    target_compile_definitions(${FULL_EXTENSION_NAME} PRIVATE NDEBUG)
endif()

# Install the extension
install(TARGETS ${FULL_EXTENSION_NAME} LIBRARY DESTINATION ${LIB_INSTALL_DIR})
