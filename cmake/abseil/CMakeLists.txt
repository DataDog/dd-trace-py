cmake_minimum_required(VERSION 3.19)
project(absl_build LANGUAGES CXX)
include(FetchContent)

# Abseil requires C++17 minimum. Extensions linking against this may use a higher standard; ABSL_PROPAGATE_CXX_STD
# ensures abseil's targets pick up the right standard from the consumer.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Must be set globally so that all abseil static libraries are position-independent and can be linked into shared Python
# extension modules.
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Platform-specific definitions required by abseil.
if(APPLE)
    # Newer macOS SDKs drop some BSD-style type definitions that abseil relies on.
    add_compile_definitions(_DARWIN_C_SOURCE)
endif()
add_compile_definitions(_POSIX_C_SOURCE=200809L)

# Abseil build knobs — disable everything we don't need.
set(ABSL_PROPAGATE_CXX_STD
    ON
    CACHE BOOL "" FORCE)
set(BUILD_TESTING
    OFF
    CACHE BOOL "" FORCE)
set(ABSL_BUILD_TESTING
    OFF
    CACHE BOOL "" FORCE)
set(ABSL_USE_GOOGLETEST_HEAD
    OFF
    CACHE BOOL "" FORCE)

# Force install rules on even though abseil is a FetchContent sub-project. Without this, cmake --install would be a
# no-op because abseil defaults ABSL_ENABLE_INSTALL to OFF when it detects it is not the top-level project.
set(ABSL_ENABLE_INSTALL
    ON
    CACHE BOOL "" FORCE)

# FETCHCONTENT_UPDATES_DISCONNECTED prevents re-fetching on every configure once the initial clone is in the cache
# directory set by FETCHCONTENT_BASE_DIR (forwarded from setup.py).
set(FETCHCONTENT_UPDATES_DISCONNECTED
    ON
    CACHE BOOL "" FORCE)
FetchContent_Declare(
    absl
    GIT_REPOSITORY https://github.com/abseil/abseil-cpp.git
    GIT_TAG 20250127.1
    GIT_SHALLOW TRUE
    GIT_PROGRESS TRUE)
FetchContent_MakeAvailable(absl)
