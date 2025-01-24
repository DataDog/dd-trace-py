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
        "v15.0.0"
        CACHE STRING "libdatadog github tag")
endif()

if(NOT DEFINED DD_CHECKSUMS)
    set(DD_CHECKSUMS
        "d5b969b293e5a9e5e36404a553bbafdd55ff6af0b089698bd989a878534df0c7 libdatadog-aarch64-alpine-linux-musl.tar.gz"
        "4540ffb8ccb671550a39ba79226117086582c1eaf9714180a9e26bd6bb175860 libdatadog-aarch64-apple-darwin.tar.gz"
        "31bceab4f56873b03b3728760d30e3abc493d32ca8fdc9e1f2ec2147ef4d5424 libdatadog-aarch64-unknown-linux-gnu.tar.gz"
        "530348c4b02cc7096de2231476ec12db82e2cc6de12a87e5b28af47ea73d4e56 libdatadog-x86_64-alpine-linux-musl.tar.gz"
        "5073ffc657bc4698f8bdd4935475734577bfb18c54dcbebc4f7d8c7595626e52 libdatadog-x86_64-unknown-linux-gnu.tar.gz")
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
