cmake_minimum_required(VERSION 3.19)
include(FetchContent)

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

# Check the DD_COMPILE_ABSEIL environment variable and build type
if(DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}" STREQUAL
                                                                                 "false"))
    message("==============================================================")
    message("WARNING: DD_COMPILE_ABSEIL set to 0 or false: not using abseil")
    message("==============================================================")
    add_definitions(-DDONT_COMPILE_ABSEIL)
elseif(CMAKE_BUILD_TYPE STREQUAL "Debug")
    message("=====================================")
    message("WARNING: Debug mode: not using abseil")
    message("=====================================")
    add_definitions(-DDONT_COMPILE_ABSEIL)
else()
    message("Release, RelWithDebInfo, or MinSizeRel mode: using abseil (DD_COMPILE_ABSEIL unset or not 0/false)")
    FetchContent_Declare(absl URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20250127.1.zip")
    FetchContent_MakeAvailable(absl)
endif()

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

# Set properties to prevent CMake from adding any prefix or suffix
set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES PREFIX "" SUFFIX "")

# Set output directory if specified
if(DEFINED LIB_INSTALL_DIR)
    set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES LIBRARY_OUTPUT_DIRECTORY ${LIB_INSTALL_DIR})
endif()

# Include directories
target_include_directories(
    ${FULL_EXTENSION_NAME}
    PRIVATE ${Python3_INCLUDE_DIRS} ${CMAKE_CURRENT_SOURCE_DIR}
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
    target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE atomic)
    set_target_properties(${FULL_EXTENSION_NAME} PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                                                            INSTALL_RPATH "$ORIGIN/../../internal/datadog/profiling")
endif()

# Link with libdd_wrapper if NATIVE_EXTENSION_LOCATION is defined
if(DEFINED NATIVE_EXTENSION_LOCATION)
    # Find the libdd_wrapper shared library
    find_library(
        DD_WRAPPER_LIB
        NAMES libdd_wrapper${EXTENSION_SUFFIX}
        PATHS ${CMAKE_CURRENT_SOURCE_DIR}/../../internal/datadog/profiling
              ${NATIVE_EXTENSION_LOCATION}/../../datadog/profiling
        NO_DEFAULT_PATH)

    if(DD_WRAPPER_LIB)
        message(STATUS "Found libdd_wrapper: ${DD_WRAPPER_LIB}")
        target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE ${DD_WRAPPER_LIB})
    else()
        message(WARNING "libdd_wrapper not found, extension may not link correctly")
    endif()
endif()

# Add NDEBUG flag for release builds
if(CMAKE_BUILD_TYPE STREQUAL "Release"
   OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo"
   OR CMAKE_BUILD_TYPE STREQUAL "MinSizeRel")
    target_compile_definitions(${FULL_EXTENSION_NAME} PRIVATE NDEBUG)
else()
    target_compile_definitions(${FULL_EXTENSION_NAME} PRIVATE UNDEBUG)
endif()

# Install the extension
install(TARGETS ${FULL_EXTENSION_NAME} LIBRARY DESTINATION ${LIB_INSTALL_DIR})
