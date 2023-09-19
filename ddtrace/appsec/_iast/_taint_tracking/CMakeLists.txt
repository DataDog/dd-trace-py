cmake_minimum_required(VERSION 3.19)
include(FetchContent)

set(APP_NAME _native)
option(BUILD_MACOS "Build for MacOS" OFF)

project(${APP_NAME})

set(CMAKE_CXX_STANDARD 17)

# -U_FORTIFY_SOURCE to fix a bug in alpine and pybind11
# https://github.com/pybind/pybind11/issues/1650
# https://gitlab.alpinelinux.org/alpine/aports/-/issues/8626
add_compile_options(-fPIC -fexceptions -fvisibility=hidden -fpermissive -pthread -Wall -Wno-unknown-pragmas -U_FORTIFY_SOURCE)

if(BUILD_MACOS)
    # https://pybind11.readthedocs.io/en/stable/compiling.html#building-manually
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif(BUILD_MACOS)
unset(BUILD_MACOS CACHE)

FetchContent_Declare(
        absl
        URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20211102.0.zip"
)
FetchContent_MakeAvailable(absl)

include_directories(".")

file(GLOB SOURCE_FILES "*.cpp"
        "Aspects/*.cpp"
        "Initializer/*.cpp"
        "TaintedOps/*.cpp"
        "TaintTracking/*.cpp"
        "Utils/*.cpp")
file(GLOB HEADER_FILES "*.h"
        "Aspects/*.h"
        "Initializer/*.h"
        "TaintedOps/*.h"
        "TaintTracking/*.h"
        "Utils/*.h"
        )

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${Python_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${Python_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${Python_INCLUDE_DIRS}")
message(STATUS "Python_EXECUTABLE = ${Python_EXECUTABLE}")

add_subdirectory(_vendor/pybind11)

pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(
        _native
        PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)

