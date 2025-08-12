cmake_minimum_required(VERSION 3.19)
project(
    dd_wrapper
    VERSION 0.1.1
    LANGUAGES CXX)

# Build in a predictable location.  This is needed for setup.py
get_filename_component(dd_wrapper_BUILD_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../ddtrace.internal.datadog.profiling"
                       ABSOLUTE)

# Custom modules are in the parent directory
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

# Includes
include(AnalysisFunc)
include(FindClangtidy)
include(FindCppcheck)
include(FindInfer)
include(CheckSymbolExists)

find_package(LibNative)

# Set verbose mode so compiler and args are shown
set(CMAKE_VERBOSE_MAKEFILE ON)

# Since this file is currently only loaded as a subdirectory, we need to propagate certain libdatadog variables up to
# the parent scope.
set(Datadog_INCLUDE_DIRS
    ${Datadog_INCLUDE_DIRS}
    PARENT_SCOPE)
set(Datadog_LIBRARIES
    ${Datadog_LIBRARIES}
    PARENT_SCOPE)

set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

if(NOT Threads_FOUND OR NOT CMAKE_USE_PTHREADS_INIT)
    message(FATAL_ERROR "pthread compatible library not found")
endif()

# Library sources
add_library(
    dd_wrapper SHARED
    src/code_provenance.cpp
    src/code_provenance_interface.cpp
    src/ddup_interface.cpp
    src/profile.cpp
    src/sample.cpp
    src/sample_manager.cpp
    src/static_sample_pool.cpp
    src/uploader.cpp
    src/uploader_builder.cpp)

# Add common configuration flags
add_ddup_config(dd_wrapper)

target_include_directories(dd_wrapper PRIVATE include ${Datadog_INCLUDE_DIRS})

target_link_libraries(dd_wrapper PRIVATE _native Threads::Threads)

# Figure out the suffix.  Try to approximate the cpython way of doing things.
check_symbol_exists(__GLIBC__ "features.h" HAVE_GLIBC)
check_symbol_exists(__MUSL__ "features.h" HAVE_MUSL)

set(PLATFORM_LIBC "unknown")
if(HAVE_GLIBC)
    set(PLATFORM_LIBC "glibc")
elseif(HAVE_MUSL)
    set(PLATFORM_LIBC "musl")
endif()

# Processor
set(PLATFORM_PROCESSOR "${CMAKE_SYSTEM_PROCESSOR}")

# Put the suffix together
set(PLATFORM_SUFFIX "${PLATFORM_LIBC}-${PLATFORM_PROCESSOR}")
string(TOLOWER ${PLATFORM_SUFFIX} PLATFORM_SUFFIX)

# target output name should combine the system name and the processor this won't necessarily match the cpython way 1-1,
# but as long as it encodes the major moving parts, it's fine

if(DEFINED EXTENSION_SUFFIX)
    set(DD_WRAPPER_TARGET_NAME "dd_wrapper${EXTENSION_SUFFIX}")
else()
    set(DD_WRAPPER_TARGET_NAME "dd_wrapper-${PLATFORM_SUFFIX}")
endif()

set_target_properties(dd_wrapper PROPERTIES OUTPUT_NAME "${DD_WRAPPER_TARGET_NAME}")
if(APPLE)
    set_target_properties(dd_wrapper PROPERTIES BUILD_RPATH "@loader_path/../../native" INSTALL_RPATH
                                                                                        "@loader_path/../../native")
    # Add a post-build step to fix the library dependency to use @rpath
    add_custom_command(
        TARGET dd_wrapper
        POST_BUILD
        COMMAND install_name_tool -change "_native${EXTENSION_SUFFIX}" "@rpath/_native${EXTENSION_SUFFIX}"
                $<TARGET_FILE:dd_wrapper>
        COMMENT "Fixing library dependency to use @rpath")
else()
    # To make sure that dd_wrapper finds _native.so for real
    set_target_properties(dd_wrapper PROPERTIES INSTALL_RPATH "$ORIGIN/../../../internal/native")
endif()

# For a regular build, the LIB_INSTALL_DIR represents the final location of the library, so nothing special is needed.
# However, for an inplace build, setup.py will pass a temporary path as the extension output directory, so while it will
# handle the extension artifacts themselves, supplementary files like this one will be left uncopied. One way around
# this is to propagate the original source dir of the extension, which can be used to deduce the ideal install
# directory.
if(INPLACE_LIB_INSTALL_DIR)
    set(LIB_INSTALL_DIR "${INPLACE_LIB_INSTALL_DIR}")
endif()

# If LIB_INSTALL_DIR is set, install the library. Install one directory up--ddup, crashtracker, and stackv2 are set to
# the same relative level.
if(LIB_INSTALL_DIR)
    install(
        TARGETS dd_wrapper
        LIBRARY DESTINATION ${LIB_INSTALL_DIR}/..
        ARCHIVE DESTINATION ${LIB_INSTALL_DIR}/..
        RUNTIME DESTINATION ${LIB_INSTALL_DIR}/..)
endif()

# Configure cppcheck
add_cppcheck_target(
    dd_wrapper
    DIRECTORY
    ${CMAKE_CURRENT_SOURCE_DIR}
    INCLUDE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${Datadog_INCLUDE_DIRS}
    SRC
    ${CMAKE_CURRENT_SOURCE_DIR}/src)

# Static analysis
add_infer_target(dd_wrapper)
add_clangtidy_target(dd_wrapper)

# Add the tests
if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(test)
endif()
