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
        "v18.0.0"
        CACHE STRING "libdatadog github tag")
endif()

if(NOT DEFINED DD_CHECKSUMS)
    set(DD_CHECKSUMS
        "4b64b58162d215a4f16b6ced4d602667565ebe20015341219daa998e3cf4e0a8 libdatadog-aarch64-alpine-linux-musl.tar.gz"
        "1b63df9650c2d087ec291198616a9bc2237b52ad532244eccbf5923a0662815b libdatadog-aarch64-apple-darwin.tar.gz"
        "f544316a2b58476979a3b05f0236837790320c385a73f1e111f8736b95ca3a87 libdatadog-aarch64-unknown-linux-gnu.tar.gz"
        "8af91ff3f7d266a6acc55b3a12a927a3d1b6ab51845b3d54333965086453c1c6 libdatadog-x86_64-alpine-linux-musl.tar.gz"
        "9402b83ecee3a73da8b4bccee1c57a3a8ac6e6d175d50fbee08d458eeda69c16 libdatadog-x86_64-apple-darwin.tar.gz"
        "c7c7f0ce597d515ce6aa8bcf3edd12a009c2c02dd5e715ea318a3bcf3221a65d libdatadog-x86_64-unknown-linux-gnu.tar.gz"
        "1d1be67b92327618bd8023abc36f51484a951c3ab069841b7cb09b5484e36f1f69c189895da5b8f16fe853c7b99cd1be5e24ff99bed195d0cfd57dd0f62b8a95 libdatadog-x64-windows.zip"
        "9015e0a4747a91d5c7334955e2d5e2006207fb683a7534b9ea65a7accdb7d24e5869a9d58df3a918e2ffc739630fac3c6d293664b7f12b32f3977d7bd12b2c44 libdatadog-x86-windows.zip"
    )
endif()

# Determine platform-specific tarball name in a way that conforms to the libdatadog naming scheme in Github releases
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(DD_ARCH "aarch64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|amd64|AMD64")
    set(DD_ARCH "x86_64")
else()
    message(FATAL_ERROR "Unsupported architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

set(DD_EXT "tar.gz")
set(DD_HASH_ALGO "SHA256")

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
elseif(WIN32)
    # WIN32 is True when it's Windows, including Win64
    set(DD_PLATFORM "windows")

    if(CMAKE_SIZEOF_VOID_P EQUAL 8)
        set(DD_ARCH "x64")
    else()
        set(DD_ARCH "x86")
    endif()

    set(DD_EXT "zip")
    set(DD_HASH_ALGO "SHA512")
else()
    message(FATAL_ERROR "Unsupported operating system")
endif()

set(DD_TARBALL "libdatadog-${DD_ARCH}-${DD_PLATFORM}.${DD_EXT}")

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

set(LIBDATADOG_URL "https://github.com/DataDog/libdatadog/releases/download/${TAG_LIBDATADOG}/${DD_TARBALL}")

if(EXISTS "${TARBALL_PATH}")
    file(${DD_HASH_ALGO} "${TARBALL_PATH}" EXISTING_HASH)

    if(NOT EXISTING_HASH STREQUAL DD_HASH)
        file(REMOVE "${TARBALL_PATH}")

        # Also remove the subbuild directory to force a fresh download
        file(REMOVE_RECURSE "${CMAKE_CURRENT_BINARY_DIR}/_deps/libdatadog-subbuild")
    endif()
endif()

# Use FetchContent to download and extract the library
FetchContent_Declare(
    libdatadog
    URL ${LIBDATADOG_URL}
    URL_HASH ${DD_HASH_ALGO}=${DD_HASH}
    DOWNLOAD_DIR "${FETCHCONTENT_DOWNLOADS_DIR}" SOURCE_DIR "${FETCHCONTENT_BASE_DIR}/libdatadog-src")

# Make the content available
FetchContent_MakeAvailable(libdatadog)

# Set up paths
get_filename_component(Datadog_ROOT "${libdatadog_SOURCE_DIR}" ABSOLUTE)
set(ENV{Datadog_ROOT} "${Datadog_ROOT}")
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
