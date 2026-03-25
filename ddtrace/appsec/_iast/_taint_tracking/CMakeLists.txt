cmake_minimum_required(VERSION 3.19)
include(FetchContent)

# AIDEV-NOTE: This CMakeLists.txt is used exclusively to build the native C++ unit tests (appsec_iast_native riot venv).
# The _native Python extension itself is now built via AbslExtension in setup.py, so there is no pybind11_add_module /
# install() here.

project(_native_tests)

set(CMAKE_CXX_STANDARD 17)

# -U_FORTIFY_SOURCE to fix a bug in alpine and pybind11 https://github.com/pybind/pybind11/issues/1650
# https://gitlab.alpinelinux.org/alpine/aports/-/issues/8626
add_compile_options(
    -fPIC
    -fexceptions
    -fvisibility=hidden
    -fpermissive
    -pthread
    -Wall
    -Wno-unknown-pragmas
    -U_FORTIFY_SOURCE
    -g)

if(APPLE)
    add_link_options(-ldl -undefined dynamic_lookup)
endif()

# In Release/RelWithDebInfo builds NDEBUG is defined and taint_range.h selects absl::node_hash_map, so we need Abseil.
# In Debug builds NDEBUG is not set and std::unordered_map is used, so Abseil is skipped.
if(NOT CMAKE_BUILD_TYPE STREQUAL "Debug")
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
endif()

include_directories(".")

file(
    GLOB
    SOURCE_FILES
    "*.cpp"
    "api/*.cpp"
    "context/*.cpp"
    "aspects/*.cpp"
    "initializer/*.cpp"
    "tainted_ops/*.cpp"
    "taint_tracking/*.cpp"
    "utils/*.cpp")
file(
    GLOB
    HEADER_FILES
    "*.h"
    "api/*.h"
    "context/*.h"
    "aspects/*.h"
    "initializer/*.h"
    "tainted_ops/*.h"
    "taint_tracking/*.h"
    "utils/*.h")

# For pybind11 3.0: Use legacy Python detection (compatible with wheel building)
set(PYBIND11_FINDPYTHON OFF)

add_subdirectory(_vendor/pybind11)

find_package(
    Python3
    COMPONENTS Interpreter Development
    REQUIRED)
set(PYTHON_EXECUTABLE ${Python3_EXECUTABLE})
set(PYTHON_INCLUDE_DIRS ${Python3_INCLUDE_DIRS})
set(PYTHON_LIBRARIES ${Python3_LIBRARIES})

add_subdirectory(tests EXCLUDE_FROM_ALL)
