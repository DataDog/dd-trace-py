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
if(NOT DEFINED TAG_LIBDATADOG)
    set(TAG_LIBDATADOG
        "v16.0.2"
        CACHE STRING "libdatadog github tag")
endif()

if(NOT DEFINED DD_CHECKSUMS)
    set(DD_CHECKSUMS
        "b1e7f971277b5c16339f36f85da38680085974415c31c0414d9b5fed78af6ae4 libdatadog-aarch64-alpine-linux-musl.tar.gz"
        "ac9944d030c24f6c570237ade27185e7da0c1018759b14af5783310fb5b75ddc libdatadog-aarch64-apple-darwin.tar.gz"
        "88cc2d1f412a1681ae1d1b77ffdb15e0199690324d233b4834ccaccc8437f275 libdatadog-aarch64-unknown-linux-gnu.tar.gz"
        "e3a6fc5a6ba145fa47d7458f5235310d8cbbba848840ac83f95728d8a0e784fd libdatadog-i686-alpine-linux-musl.tar.gz"
        "be3f64641e6c887c8eee05b705701ba051a3c382c52e7df1e74876a0fcf9e66a libdatadog-i686-unknown-linux-gnu.tar.gz"
        "5717af124b2d4187376676f2da7d01e429e481b690eaafe1580181e89b87ee15 libdatadog-x86_64-alpine-linux-musl.tar.gz"
        "155c96ccee4df4b4c90fba047d07c9d83049058325d63d70f15e83d040abd561 libdatadog-x86_64-apple-darwin.tar.gz"
        "acf8273bda559700517c6b2de8e6a95b4608ef0200e6c7749507e90b5e75d6cb libdatadog-x86_64-unknown-linux-gnu.tar.gz")
endif()

# Determine platform-specific tarball name in a way that conforms to the libdatadog naming scheme in Github releases
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(DD_ARCH "aarch64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64")
    set(DD_ARCH "x86_64")
else()
    message(FATAL_ERROR "Unsupported architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

if(APPLE)
    set(DD_PLATFORM "apple-darwin")
elseif(UNIX)
    execute_process(
        COMMAND ldd --version
        OUTPUT_VARIABLE LDD_OUTPUT
        ERROR_VARIABLE LDD_OUTPUT
        OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(LDD_OUTPUT MATCHES "musl")
        set(DD_PLATFORM "alpine-linux-musl")
    else()
        set(DD_PLATFORM "unknown-linux-gnu")
    endif()
else()
    message(FATAL_ERROR "Unsupported operating system")
endif()

set(DD_TARBALL "libdatadog-${DD_ARCH}-${DD_PLATFORM}.tar.gz")

# Make sure we can get the checksum for the tarball
foreach(ENTRY IN LISTS DD_CHECKSUMS)
    if(ENTRY MATCHES "^([a-fA-F0-9]+) ${DD_TARBALL}$")
        set(DD_HASH "${CMAKE_MATCH_1}")
        break()
    endif()
endforeach()

if(NOT DEFINED DD_HASH)
    message(FATAL_ERROR "Could not find checksum for ${DD_TARBALL}")
endif()

# Clean up any existing downloads if they exist
set(TARBALL_PATH "${FETCHCONTENT_DOWNLOADS_DIR}/${DD_TARBALL}")
if(EXISTS "${TARBALL_PATH}")
    file(SHA256 "${TARBALL_PATH}" EXISTING_HASH)
    if(NOT EXISTING_HASH STREQUAL DD_HASH)
        file(REMOVE "${TARBALL_PATH}")
        # Also remove the subbuild directory to force a fresh download
        file(REMOVE_RECURSE "${CMAKE_CURRENT_BINARY_DIR}/_deps/libdatadog-subbuild")
    endif()
endif()

# Use FetchContent to download and extract the library
FetchContent_Declare(
    libdatadog
    URL "https://github.com/DataDog/libdatadog/releases/download/${TAG_LIBDATADOG}/${DD_TARBALL}"
    URL_HASH SHA256=${DD_HASH}
    DOWNLOAD_DIR "${FETCHCONTENT_DOWNLOADS_DIR}" SOURCE_DIR "${FETCHCONTENT_BASE_DIR}/libdatadog-src")

# Make the content available
FetchContent_MakeAvailable(libdatadog)

# Set up paths
get_filename_component(Datadog_ROOT "${libdatadog_SOURCE_DIR}" ABSOLUTE)
set(Datadog_DIR "${Datadog_ROOT}/cmake")

# Configure library preferences (static over shared)
set(CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP ${CMAKE_FIND_LIBRARY_SUFFIXES})
set(CMAKE_FIND_LIBRARY_SUFFIXES .a)

# Find the package
find_package(Datadog REQUIRED)

# Restore library preferences
set(CMAKE_FIND_LIBRARY_SUFFIXES ${CMAKE_FIND_LIBRARY_SUFFIXES_BACKUP})
