cmake_minimum_required(VERSION 3.19)
project(dd_wrapper
    VERSION 0.1.0
    LANGUAGES CXX
)

# C++17
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -Wall -Werror -Wextra -Wshadow")

# If the compiler supports fanalyzer, let's also use that
if (CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    if (CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL "10.0")
        set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -fanalyzer")
    endif()
endif()

# Infer some basic properties about the build.  This is necessary because multiple
# extensions reuse this cmake file, so we need its artifacts to go in a consistent
# place
get_filename_component(dd_wrapper_BUILD_PARENT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
set(dd_wrapper_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/ddtrace.internal.datadog.profiling" PARENT_SCOPE)

# Setup state for FindLibdatadog
set(Datadog_BUILD_DIR "${dd_wrapper_BUILD_PARENT}/libdatadog")
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Includes
include(CMakePackageConfigHelpers) # For generating the config file
include(FetchContent)
include(ExternalProject)
include(FindLibdatadog)
include(FindPython3)

# Send some of these vars back up to the parent
set(Datadog_INCLUDE_DIRS "${Datadog_INCLUDE_DIRS}" PARENT_SCOPE)

# Library sources
add_library(dd_wrapper SHARED
    src/exporter.cpp
    src/interface.cpp
)

target_include_directories(dd_wrapper PRIVATE
    include
    ${Datadog_INCLUDE_DIRS}
    ${Python3_INCLUDE_DIRS}
)
target_link_libraries(dd_wrapper PRIVATE
    ${Datadog_LIBRARIES}
)
set_target_properties(dd_wrapper PROPERTIES POSITION_INDEPENDENT_CODE ON)

set_target_properties(dd_wrapper
    PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${dd_wrapper_BUILD_DIR}"
)

# Assumed dd_wrapper_INSTALL_DIR is propagated from the parent
message(STATUS "dd_wrapper_INSTALL_DIR: ${dd_wrapper_INSTALL_DIR}")
install(TARGETS dd_wrapper
    DESTINATION ${dd_wrapper_INSTALL_DIR}
)
