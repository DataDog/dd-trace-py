cmake_minimum_required(VERSION 3.24)

project(_native)
set(CMAKE_CXX_STANDARD 17)

option(BUILD_MACOS "Build for MacOS" OFF)
option(NATIVE_TESTING "Load test subdirectories and targets" ON)

# Compile options
add_compile_options(
    -fPIC
    -fexceptions
    -fvisibility=hidden
    -fpermissive
    -pthread
    -Wall
    -Wno-unknown-pragmas
    -U_FORTIFY_SOURCE # workaround for Alpine + pybind11
)

if(BUILD_MACOS)
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif()
unset(BUILD_MACOS CACHE)

# Optional: ccache integration
find_program(CCACHE_PROGRAM sccache)
if(CCACHE_PROGRAM)
    message(STATUS "Using ccache: ${CCACHE_PROGRAM}")
    set_property(GLOBAL PROPERTY RULE_LAUNCH_COMPILE "${CCACHE_PROGRAM}")
endif()

# FetchContent dependencies
include(FetchContent)
set(FETCHCONTENT_BASE_DIR
    ${CMAKE_BINARY_DIR}/_deps
    CACHE PATH "Dependency cache dir")

# Conditionally include absl
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
    message("Release mode: using abseil")
    FetchContent_Declare(absl URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20250127.1.zip"
                                  DOWNLOAD_EXTRACT_TIMESTAMP true)
    FetchContent_MakeAvailable(absl)

    # Turn off LTO on all Abseil targets after they're declared
    get_property(
        absl_targets
        DIRECTORY "${absl_SOURCE_DIR}"
        PROPERTY BUILDSYSTEM_TARGETS)
    foreach(tgt ${absl_targets})
        set_target_properties(${tgt} PROPERTIES INTERPROCEDURAL_OPTIMIZATION FALSE)
    endforeach()
endif()

# pybind11 via FetchContent
FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.12.0
    DOWNLOAD_EXTRACT_TIMESTAMP true)
FetchContent_MakeAvailable(pybind11)

# Source files (explicit list for better incremental builds and caching)
set(SOURCE_FILES
    # Main module
    ${CMAKE_CURRENT_SOURCE_DIR}/native.cpp
    # Aspects
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_extend.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_format.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_index.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_join.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_modulo.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_operator_add.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_slice.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_split.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_str.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspects_os_path.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/helpers.cpp
    # Initializer
    ${CMAKE_CURRENT_SOURCE_DIR}/initializer/initializer.cpp
    # Taint tracking
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/source.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/taint_range.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/tainted_object.cpp
    # Tainted operations
    ${CMAKE_CURRENT_SOURCE_DIR}/tainted_ops/tainted_ops.cpp
    # Utils
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/generic_utils.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/python_error_guard.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/string_utils.cpp)

# Header files (explicit list for better dependency tracking)
set(HEADER_FILES
    # Main headers
    ${CMAKE_CURRENT_SOURCE_DIR}/constants.h
    # Aspects
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_extend.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_format.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_index.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_join.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_modulo.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_operator_add.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_slice.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_split.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspect_str.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspects_exports.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/aspects_os_path.h
    ${CMAKE_CURRENT_SOURCE_DIR}/aspects/helpers.h
    # Initializer
    ${CMAKE_CURRENT_SOURCE_DIR}/initializer/_initializer.h
    ${CMAKE_CURRENT_SOURCE_DIR}/initializer/initializer.h
    # Taint tracking
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/source.h
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/taint_range.h
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/taint_tracking.h
    ${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/tainted_object.h
    # Tainted operations
    ${CMAKE_CURRENT_SOURCE_DIR}/tainted_ops/tainted_ops.h
    # Utils
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/generic_utils.h
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/python_error_guard.h
    ${CMAKE_CURRENT_SOURCE_DIR}/utils/string_utils.h)

# Subdirectory for native testing (optional)
if(NATIVE_TESTING)
    add_subdirectory(tests EXCLUDE_FROM_ALL)
endif()

# Define pybind11 module
pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})
get_filename_component(PARENT_DIR ${CMAKE_CURRENT_LIST_DIR} DIRECTORY)

if(NOT SOURCE_FILES)
    message(FATAL_ERROR "SOURCE_FILES is empty")
endif()

# Explicitly state this is C++
set_target_properties(_native PROPERTIES LINKER_LANGUAGE CXX)

target_include_directories(_native PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

# Link absl if enabled
if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug")
   AND NOT (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}"
                                                                                          STREQUAL "false")))
    target_link_libraries(_native PRIVATE absl::node_hash_map)
endif()

# Output directory
set_target_properties(_native PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_LIST_DIR}")

# Optional install target
install(
    TARGETS _native
    DESTINATION LIBRARY
    DESTINATION ${LIB_INSTALL_DIR}
    ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
    RUNTIME DESTINATION ${LIB_INSTALL_DIR})
