cmake_minimum_required(VERSION 3.11...3.22)
include(FetchContent)

set(APP_NAME _native)

project(${APP_NAME})

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(ABSL_PROPAGATE_CXX_STD ON)

add_compile_options(-fPIC -fexceptions -fvisibility=hidden -pthread -Wall -Wno-unknown-pragmas)

# For release modes: switch off assertions (more risky but faster). Compiler: -DNDEBUG
add_compile_definitions($<$<NOT:$<CONFIG:Debug>>:NDEBUG>)

# -U_FORTIFY_SOURCE to fix a bug in alpine and pybind11
# https://github.com/pybind/pybind11/issues/1650
# https://gitlab.alpinelinux.org/alpine/aports/-/issues/8626
add_compile_options(-U_FORTIFY_SOURCE)

add_link_options(-ldl -static-libgcc -static-libstdc++)

FetchContent_Declare(
        absl
        URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20211102.0.zip"
)
FetchContent_MakeAvailable(absl)

include_directories(".")
# get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
file(GLOB SOURCE_FILES "*.cpp"
        "Aspects/*.cpp"
        "Context/*.cpp"
        "Exceptions/*.cpp"
        "Initializer/*.cpp"
        "TaintedOps/*.cpp"
        "TaintTracking/*.cpp")
file(GLOB HEADER_FILES "*.h"
        "Aspects/*.h"
        "Context/*.h"
        "Exceptions/*.h"
        "Initializer/*.h"
        "TaintedOps/*.h"
        "TaintTracking/*.h")

set(Python_USE_STATIC_LIBS FALSE)
find_package(Python COMPONENTS Interpreter Development)

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

find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(
        _native
        PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}"
)

target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)

# add_library(${APP_NAME} SHARED ${SOURCE_FILES} ${HEADER_FILES} _native.cpp Constants.h)
#
# get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
# set_target_properties(
#         _native
#         PROPERTIES
#         LIBRARY_OUTPUT_DIRECTORY "${PARENT_DIR}"
# )

# target_link_libraries(${APP_NAME} absl::node_hash_map ${PYTHON_LIBRARIES} )
