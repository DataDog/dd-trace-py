# Only proceed if Datadog::Profiling (provided by libdatadog) isn't already defined
if(TARGET Datadog::Profiling)
    return()
endif()

# Set the FetchContent paths early
set(FETCHCONTENT_BASE_DIR
    "${CMAKE_CURRENT_BINARY_DIR}/_deps"
    CACHE PATH "FetchContent base directory")
set(FETCHCONTENT_DOWNLOADS_DIR
    "${FETCHCONTENT_BASE_DIR}/downloads"
    CACHE PATH "FetchContent downloads directory")

include_guard(GLOBAL)
include(FetchContent)

# Set version if not already set
if(NOT DEFINED LIBDATADOG_GIT_TAG)
    set(LIBDATADOG_GIT_TAG
        "f2caee9c734d9d741fc8aef6b24f95488f930210" # taegyunkim/musllinux-fix
        CACHE STRING "libdatadog github tag")
endif()

# Use FetchContent to download and extract the library
FetchContent_Declare(
    libdatadog
    GIT_REPOSITORY "https://github.com/DataDog/libdatadog.git"
    GIT_TAG "${LIBDATADOG_GIT_TAG}"
    DOWNLOAD_DIR "${FETCHCONTENT_DOWNLOADS_DIR}" SOURCE_DIR "${FETCHCONTENT_BASE_DIR}/libdatadog-src")

# Make the content available
FetchContent_MakeAvailable(libdatadog)

# Manage the output folder
set(LIBDD_OUTPUT_FOLDER "${CMAKE_CURRENT_BINARY_DIR}/libdatadog-output")
if(EXISTS "${LIBDD_OUTPUT_FOLDER}")
    file(REMOVE_RECURSE "${LIBDD_OUTPUT_FOLDER}")
endif()
file(MAKE_DIRECTORY "${LIBDD_OUTPUT_FOLDER}")

if(NOT DEFINED ENV{CARGO_BUILD_JOBS})
    set(ENV{CARGO_BUILD_JOBS} 8)
endif()

set(CARGO_PROFILE "release")
if(DEFINED CMAKE_BUILD_TYPE)
    string(TOLOWER ${CMAKE_BUILD_TYPE} BUILD_TYPE)
    if(BUILD_TYPE STREQUAL "debug")
        set(CARGO_PROFILE "dev")
    endif()
endif()

# Run the build
execute_process(
    COMMAND cargo run --bin release --features profiling,crashtracker --profile ${CARGO_PROFILE} -- --out
            ${LIBDD_OUTPUT_FOLDER}
    WORKING_DIRECTORY "${libdatadog_SOURCE_DIR}"
    RESULT_VARIABLE CARGO_RESULT)
if(NOT CARGO_RESULT EQUAL 0)
    message(FATAL_ERROR "Failed to build libdatadog FFI")
endif()

# Set up paths for the end user
set(Datadog_DIR "${LIBDD_OUTPUT_FOLDER}/cmake")
set(Datadog_LIBRARY "${LIBDD_OUTPUT_FOLDER}/lib/libdatadog_profiling${CMAKE_STATIC_LIBRARY_SUFFIX}")
set(Datadog_INCLUDE_DIR "${LIBDD_OUTPUT_FOLDER}/include")

if(NOT EXISTS ${Datadog_LIBRARY} OR NOT EXISTS ${Datadog_INCLUDE_DIR})
    message(FATAL_ERROR "Built libdatadog but couldn't find library or include files in ${LIBDD_OUTPUT_FOLDER}")
endif()

# Set up paths
get_filename_component(Datadog_ROOT "${LIBDD_OUTPUT_FOLDER}" ABSOLUTE)
set(Datadog_DIR "${Datadog_ROOT}/cmake")

# Configure library preferences (static over shared)
if(NOT WIN32)
    set(CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP ${CMAKE_FIND_LIBRARY_SUFFIXES})
    set(CMAKE_FIND_LIBRARY_SUFFIXES .a)
endif()

# Find the package
find_package(Datadog REQUIRED)

if(NOT WIN32)
    # Restore library preferences
    set(CMAKE_FIND_LIBRARY_SUFFIXES ${CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP})
endif()
