cmake_minimum_required(VERSION 3.19)
include(FetchContent)

set(APP_NAME _native)
option(BUILD_MACOS "Build for MacOS" OFF)
if(DEFINED ENV{CIBUILDWHEEL} OR DEFINED ENV{CIBW_BUILD})
    option(NATIVE_TESTING "Disable test subdirectories and targets" OFF)
else()
    option(NATIVE_TESTING "Load test subdirectories and targets" ON)
endif()

project(${APP_NAME})

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
    -U_FORTIFY_SOURCE)

if(BUILD_MACOS)
    # https://pybind11.readthedocs.io/en/stable/compiling.html#building-manually
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif(BUILD_MACOS)
unset(BUILD_MACOS CACHE)

# Check the DD_COMPILE_ABSEIL environment variable and build type
if(DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}" STREQUAL
                                                                                 "false"))
    message("==============================================================")
    message("WARNING: DD_COMPILE_ABSEIL set to 0 or false: not using abseil")
    message("==============================================================")
    add_definitions(-DDONT_COMPILE_ABSEIL) # Define DONT_COMPILE_ABSEIL preprocessor variable
elseif(CMAKE_BUILD_TYPE STREQUAL "Debug")
    message("=====================================")
    message("WARNING: Debug mode: not using abseil")
    message("=====================================")
    add_definitions(-DDONT_COMPILE_ABSEIL) # Define DONT_COMPILE_ABSEIL preprocessor variable
else()
    message("Release, RelWithDebInfo, or MinSizeRel mode: using abseil (DD_COMPILE_ABSEIL unset or not 0/false)")
    FetchContent_Declare(absl URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20250127.1.zip")
    FetchContent_MakeAvailable(absl)
endif()

include_directories(".")

file(
    GLOB
    SOURCE_FILES
    "*.cpp"
    "aspects/*.cpp"
    "initializer/*.cpp"
    "tainted_ops/*.cpp"
    "taint_tracking/*.cpp"
    "utils/*.cpp")
file(
    GLOB
    HEADER_FILES
    "*.h"
    "aspects/*.h"
    "initializer/*.h"
    "tainted_ops/*.h"
    "taint_tracking/*.h"
    "utils/*.h")

# Find ICU library (not needed for now) find_package(ICU REQUIRED COMPONENTS uc i18n)  # 'uc' for the common library,
# 'i18n' for the internationalization library include_directories(${ICU_INCLUDE_DIRS}) list(APPEND ICU_LIBS
# ${ICU_LIBRARIES})

# Debug messages
message(STATUS "PYTHON_LIBRARIES = ${PYTHON_LIBRARIES}")
message(STATUS "PYTHON_EXECUTABLE = ${PYTHON_EXECUTABLE}")
message(STATUS "PYTHON_INCLUDE_DIRS = ${PYTHON_INCLUDE_DIRS}")
# message(STATUS "ICU_LIBRARIES = ${ICU_LIBRARIES}") message(STATUS "ICU_INCLUDE_DIRS = ${ICU_INCLUDE_DIRS}")

add_subdirectory(_vendor/pybind11)
if(NATIVE_TESTING)
    find_package(
        Python3
        COMPONENTS Interpreter Development
        REQUIRED)
    # Legacy variables expected elsewhere in the project/tests
    set(PYTHON_EXECUTABLE ${Python3_EXECUTABLE})
    set(PYTHON_INCLUDE_DIRS ${Python3_INCLUDE_DIRS})
    set(PYTHON_LIBRARIES ${Python3_LIBRARIES})

    add_subdirectory(tests EXCLUDE_FROM_ALL)
endif()

# Set verbose mode so compiler and args are shown
set(CMAKE_VERBOSE_MAKEFILE ON)

pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)
set_target_properties(_native PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")

# target_link_libraries(_native PRIVATE ${ICU_LIBS})

if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug")
   AND NOT (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}"
                                                                                          STREQUAL "false")))
    target_link_libraries(${APP_NAME} PRIVATE absl::node_hash_map)
endif()

install(
    TARGETS _native
    DESTINATION LIBRARY
    DESTINATION ${LIB_INSTALL_DIR}
    ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
    RUNTIME DESTINATION ${LIB_INSTALL_DIR})
