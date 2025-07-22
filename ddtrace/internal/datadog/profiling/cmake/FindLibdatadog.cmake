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
        "v19.1.0"
        CACHE STRING "libdatadog github tag")
endif()

if(NOT DEFINED DD_CHECKSUMS)
    set(DD_CHECKSUMS
        "7c69a37cb335260610b61ae956192a6dbd104d05a8278c8ff894dbfebc2efd53 libdatadog-aarch64-alpine-linux-musl.tar.gz"
        "b992a11b90ec5927646a0c96b74fe9fcd63e7e471307e74a670ddf42fc10eaf9 libdatadog-aarch64-apple-darwin.tar.gz"
        "606b23f4de7defacd5d4a381816f8d7bfe26112c97fcdf21ec2eb998a6c5fbbd libdatadog-aarch64-unknown-linux-gnu.tar.gz"
        "2008886021ddee573c0d539626d1d58d41e2a7dbc8deca22b3662da52de6f4d9 libdatadog-x86_64-alpine-linux-musl.tar.gz"
        "6a12ef60fd7b00544343c2b6761ef801ad2e1237075711bd16dfb7247464bc43 libdatadog-x86_64-apple-darwin.tar.gz"
        "4e5b05515ab180aec0819608aa5d277ff710055819654147a9d69caea27a0dbc libdatadog-x86_64-unknown-linux-gnu.tar.gz"
        "9b33697d3a9949c81a5eadf6fd6334adba4b262acb8887651021443c5f5b35b06dc7e2bdde5cee1b7a1f4862f0896436130b7f4940050e4dbccf8e7e0b2f129e libdatadog-x64-windows.zip"
        "ace14ad5b0525c11f86d166b5696b8dcfd48cb5bb1c4435e71eba982c3da21c4f42ac6b6ec5fdf53f09a353a3339e2a31cc57003637578bc73815def808768b1 libdatadog-x86-windows.zip"
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
