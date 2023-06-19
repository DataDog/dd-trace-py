cmake_minimum_required(VERSION 3.11...3.22)
include(FetchContent)

set(APP_NAME _native)
option(BUILD_MACOS "Build for MacOS" OFF)

project(${APP_NAME})

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(ABSL_PROPAGATE_CXX_STD ON)

# For release modes: switch off assertions (more risky but faster). Compiler: -DNDEBUG
add_compile_definitions($<$<NOT:$<CONFIG:Debug>>:NDEBUG>)


if(BUILD_MACOS)
    # https://pybind11.readthedocs.io/en/stable/compiling.html#building-manually
    message(STATUS "Compile options for MacOS")
    add_compile_options(-fPIC -fexceptions -fvisibility=hidden -fpermissive -pthread -Wall -Wno-unknown-pragmas -undefined dynamic_lookup)
else()
    # -U_FORTIFY_SOURCE to fix a bug in alpine and pybind11
    # https://github.com/pybind/pybind11/issues/1650
    # https://gitlab.alpinelinux.org/alpine/aports/-/issues/8626
    message(STATUS "Compile options for Linux/Win")
    add_compile_options(-fPIC -fexceptions -fvisibility=hidden -fpermissive -pthread -Wall -Wno-unknown-pragmas -U_FORTIFY_SOURCE)
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
        "Context/*.cpp"
        "Exceptions/*.cpp"
        "Initializer/*.cpp"
        "TaintedOps/*.cpp"
        "TaintedObject/*.cpp"
        "TaintTracking/*.cpp"
        "Utils/*.cpp")
file(GLOB HEADER_FILES "*.h"
        "Aspects/*.h"
        "Context/*.h"
        "Exceptions/*.h"
        "Initializer/*.h"
        "TaintedOps/*.h"
        "TaintedOps/*.h"
        "TaintTracking/*.h"
        "Utils/*.h"
        )
set(Python_USE_STATIC_LIBS FALSE)
find_package(Python COMPONENTS Interpreter Development)
# add_link_options(-ldl -static-libgcc -static-libstdc++)
# PythonLibs is needed for pybind11::embed
# find_package(PythonLibs REQUIRED)

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${Python_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${Python_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${Python_INCLUDE_DIRS}")
message(STATUS "Python_EXECUTABLE = ${Python_EXECUTABLE}")




execute_process(
        COMMAND ${Python_EXECUTABLE} -c import\ pybind11\;print\(pybind11.get_cmake_dir\(\)\)
        OUTPUT_VARIABLE CMAKE_PREFIX_PATH
        RESULT_VARIABLE _pybind11_STATUS
        OUTPUT_STRIP_TRAILING_WHITESPACE)
if(_pybind11_STATUS EQUAL "0")
    message(STATUS "Found pybind11: ${CMAKE_PREFIX_PATH}")
else()
    message(FATAL_ERROR "pybind11 not found")
endif()
unset(_pybind11_STATUS)

find_package(pybind11)

pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(
        _native
        PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)
target_link_libraries(${APP_NAME} PRIVATE pybind11::embed)