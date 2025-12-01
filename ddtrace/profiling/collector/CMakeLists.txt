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

# AIDEV-NOTE: Configure paths for Rust cxx bridge integration
# The cxx bridge headers are generated during the Rust build and need to be
# accessible to the C++ compilation. The actual bridge code is compiled into
# the Rust library (_native.so) that we link against.
if(NOT DEFINED RUST_TARGET_DIR)
    # Default to the target directory relative to the project root
    set(RUST_TARGET_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}")
endif()

# Determine the Rust build type
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(RUST_BUILD_TYPE "debug")
else()
    set(RUST_BUILD_TYPE "release")
endif()

# Find the cxx-generated headers directory
# The cxx-build crate puts generated files in target/{build_type}/build/{crate}-{hash}/out/cxxbridge/include/
# We need to find the directory with the hash
file(GLOB CXX_BRIDGE_DIRS "${RUST_TARGET_DIR}/${RUST_BUILD_TYPE}/build/ddtrace-native-*/out/cxxbridge/include")
list(LENGTH CXX_BRIDGE_DIRS CXX_BRIDGE_DIRS_COUNT)
if(CXX_BRIDGE_DIRS_COUNT GREATER 0)
    list(GET CXX_BRIDGE_DIRS 0 CXX_BRIDGE_DIR)
    message(STATUS "Found cxx bridge headers at: ${CXX_BRIDGE_DIR}")
else()
    message(WARNING "Could not find cxx bridge headers in ${RUST_TARGET_DIR}/${RUST_BUILD_TYPE}/build/")
    set(CXX_BRIDGE_DIR "")
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

# AIDEV-NOTE: Add include directories for cxx bridge
# We need:
# 1. The cxxbridge directory for generated headers like memalloc.rs.h
# 2. The rust/cxx.h header from the cxx crate (typically in the cargo registry)
#    which is found via the cxxbridge directory structure
# Include directories
target_include_directories(
    ${FULL_EXTENSION_NAME}
    PRIVATE ${Python3_INCLUDE_DIRS} ${CMAKE_CURRENT_SOURCE_DIR}
            ${CMAKE_CURRENT_SOURCE_DIR}/../../internal/datadog/profiling/dd_wrapper/include
            ${RUST_GENERATED_HEADERS_DIR}
            ${CXX_BRIDGE_DIR})

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

# AIDEV-NOTE: Link against the cxx bridge static library
# The cxx-build generates a static library containing the bridge code.
# We need to find and link against libmemalloc_cxx_bridge.a (or .lib on Windows)
file(GLOB CXX_BRIDGE_LIB_DIRS "${RUST_TARGET_DIR}/${RUST_BUILD_TYPE}/build/ddtrace-native-*/out")
list(LENGTH CXX_BRIDGE_LIB_DIRS CXX_BRIDGE_LIB_DIRS_COUNT)
if(CXX_BRIDGE_LIB_DIRS_COUNT GREATER 0)
    # Try all directories to find the one with the library
    # (there may be multiple due to incremental builds with different hashes)
    set(CXX_BRIDGE_STATIC_LIB "")
    foreach(dir ${CXX_BRIDGE_LIB_DIRS})
        find_library(
            CXX_BRIDGE_STATIC_LIB_TEMP
            NAMES memalloc_cxx_bridge
            PATHS ${dir}
            NO_DEFAULT_PATH
        )
        if(CXX_BRIDGE_STATIC_LIB_TEMP)
            set(CXX_BRIDGE_STATIC_LIB ${CXX_BRIDGE_STATIC_LIB_TEMP})
            set(CXX_BRIDGE_OUT_DIR ${dir})
            break()
        endif()
        unset(CXX_BRIDGE_STATIC_LIB_TEMP CACHE)
    endforeach()
    
    if(CXX_BRIDGE_STATIC_LIB)
        message(STATUS "Found cxx bridge static library: ${CXX_BRIDGE_STATIC_LIB}")
        target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE ${CXX_BRIDGE_STATIC_LIB})
        
        # AIDEV-NOTE: The cxx bridge references Rust symbols from the _native library.
        # On macOS, we link against the library directly and use rpath at runtime.
        # On Linux, we can't link against lib_native.so because it gets renamed
        # (to _native.cpython-XYZ.so) when installed, so we rely on Python's dynamic
        # loader to resolve the symbols at runtime since _native is always loaded first.
        if(APPLE)
            # On macOS, link against the Rust library
            find_library(
                RUST_NATIVE_LIB
                NAMES _native lib_native
                PATHS ${RUST_TARGET_DIR}/${RUST_BUILD_TYPE}
                      ${CMAKE_CURRENT_SOURCE_DIR}/../../../ddtrace/internal/native
                NO_DEFAULT_PATH
            )
            
            if(RUST_NATIVE_LIB)
                message(STATUS "Found Rust native library: ${RUST_NATIVE_LIB}")
                target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE ${RUST_NATIVE_LIB})
            else()
                message(WARNING "Rust native library not found - symbols may be unresolved")
            endif()
        else()
            # On Linux, don't link - let symbols be resolved at runtime
            message(STATUS "On Linux: Rust symbols will be resolved at runtime via Python's loader")
            # Make sure undefined symbols don't cause link errors
            if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
                target_link_options(${FULL_EXTENSION_NAME} PRIVATE "-Wl,--allow-shlib-undefined")
            endif()
        endif()
    else()
        message(WARNING "cxx bridge static library not found")
    endif()
else()
    message(WARNING "Could not find cxx bridge build output directory")
endif()

# Platform-specific settings
if(APPLE)
    # macOS specific - set rpath for libdd_wrapper, Rust _native library, and use dynamic lookup for Python symbols
    # AIDEV-NOTE: Added @loader_path/../../internal/native for the Rust cxx bridge integration
    set_target_properties(
        ${FULL_EXTENSION_NAME}
        PROPERTIES BUILD_WITH_INSTALL_RPATH TRUE
                   INSTALL_RPATH "@loader_path/../../internal/native;@loader_path/../../internal/datadog/profiling"
                   LINK_FLAGS "-undefined dynamic_lookup")
elseif(UNIX)
    # Linux specific
    target_link_libraries(${FULL_EXTENSION_NAME} PRIVATE atomic)
    # AIDEV-NOTE: On Linux, the Rust symbols are resolved at runtime by Python's loader,
    # so we don't need to add the native directory to rpath (only need libdd_wrapper)
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

# Optional: Build tests if BUILD_TESTING is ON
option(BUILD_TESTING "Build tests" OFF)
if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
