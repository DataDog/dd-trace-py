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
    -U_FORTIFY_SOURCE  # workaround for Alpine + pybind11
)

if(BUILD_MACOS)
    message(STATUS "Compile options for MacOS")
    add_link_options(-ldl -undefined dynamic_lookup)
else()
    message(STATUS "Compile options for Linux/Win")
endif()
unset(BUILD_MACOS CACHE)

# Optional: ccache integration
find_program(CCACHE_PROGRAM ccache)
if(CCACHE_PROGRAM)
    message(STATUS "Using ccache: ${CCACHE_PROGRAM}")
    set_property(GLOBAL PROPERTY RULE_LAUNCH_COMPILE "${CCACHE_PROGRAM}")
endif()

# FetchContent dependencies
include(FetchContent)
set(FETCHCONTENT_BASE_DIR ${CMAKE_BINARY_DIR}/_deps CACHE PATH "Dependency cache dir")

# Conditionally include absl
if(DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}" STREQUAL "false"))
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
    FetchContent_Declare(
        absl
        URL "https://github.com/abseil/abseil-cpp/archive/refs/tags/20250127.1.zip"
        DOWNLOAD_EXTRACT_TIMESTAMP true
    )
    FetchContent_MakeAvailable(absl)
endif()

# pybind11 via FetchContent
FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.12.0
    DOWNLOAD_EXTRACT_TIMESTAMP true
)
FetchContent_MakeAvailable(pybind11)

# Source and header files
file(GLOB CONFIGURE_DEPENDS
    SOURCE_FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/aspects/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/initializer/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/tainted_ops/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/utils/*.cpp"
)
file(GLOB CONFIGURE_DEPENDS
    HEADER_FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/aspects/*.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/initializer/*.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/tainted_ops/*.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/taint_tracking/*.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/utils/*.h"
)
message(STATUS "CMAKE_CURRENT_SOURCE_DIR = ${CMAKE_CURRENT_SOURCE_DIR}")
if(NOT SOURCE_FILES)
    message(FATAL_ERROR "No source files found! Check your GLOB paths.")
endif()


# Subdirectory for native testing (optional)
if(NATIVE_TESTING)
    add_subdirectory(tests EXCLUDE_FROM_ALL)
endif()

# Define pybind11 module
pybind11_add_module(_native SHARED ${SOURCE_FILES} ${HEADER_FILES})

target_include_directories(_native PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

# Link absl if enabled
if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug") AND NOT (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}" STREQUAL "false")))
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
    RUNTIME DESTINATION ${LIB_INSTALL_DIR}
)
