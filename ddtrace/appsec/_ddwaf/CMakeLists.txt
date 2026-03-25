# ddtrace/appsec/_ddwaf/CMakeLists.txt Downloads and stages the libddwaf pre-built shared library at cmake configure
# time, then installs it into the wheel under ddtrace/appsec/_ddwaf/libddwaf/.
#
# AIDEV-NOTE: libddwaf is a pre-built C library shipped inside the wheel. We download the correct platform tarball(s) at
# cmake configure time using file(DOWNLOAD) — no Python pre-build hook is needed, and CMake's built-in SHA256
# verification replaces the Python checksum logic in setup.py.
#
# The staging directory mirrors what setup.py used: ddtrace/appsec/_ddwaf/libddwaf/{arch}/lib/libddwaf.{ext} The CMake
# install rule preserves this layout in the wheel so the existing Python loader (ddwaf.py) can resolve the library via
# its relative path.

set(_ddwaf_version "1.30.1")
set(_ddwaf_url_root "https://github.com/DataDog/libddwaf/releases/download")
set(_ddwaf_stage_dir "${CMAKE_CURRENT_SOURCE_DIR}/libddwaf")
set(_ddwaf_cache_dir "${CMAKE_SOURCE_DIR}/.download_cache")

# Per-platform archive name and shared-library suffix.
if(WIN32)
    set(_ddwaf_lib_suffix ".dll")
    if(CMAKE_SIZEOF_VOID_P EQUAL 8)
        if(CMAKE_SYSTEM_PROCESSOR MATCHES "ARM64|arm64|aarch64")
            set(_ddwaf_archs "arm64")
        else()
            set(_ddwaf_archs "x64")
        endif()
    else()
        set(_ddwaf_archs "win32")
    endif()
elseif(APPLE)
    set(_ddwaf_lib_suffix ".dylib")
    # Universal2 wheels need both slices; single-arch builds pick one.
    set(_plat "$ENV{PLAT}")
    if(_plat MATCHES "universal2")
        set(_ddwaf_archs "arm64;x86_64")
    elseif(_plat MATCHES "arm64")
        set(_ddwaf_archs "arm64")
    elseif(_plat MATCHES "x86_64")
        set(_ddwaf_archs "x86_64")
    else()
        # Fall back to host architecture.
        if(CMAKE_SYSTEM_PROCESSOR MATCHES "arm64|aarch64")
            set(_ddwaf_archs "arm64")
        else()
            set(_ddwaf_archs "x86_64")
        endif()
    endif()
else()
    # Linux — only the host arch.
    set(_ddwaf_lib_suffix ".so")
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
        set(_ddwaf_archs "aarch64")
    else()
        set(_ddwaf_archs "x86_64")
    endif()
endif()

foreach(_arch IN LISTS _ddwaf_archs)
    set(_arch_lib_dir "${_ddwaf_stage_dir}/${_arch}/lib")
    set(_lib_file "${_arch_lib_dir}/libddwaf${_ddwaf_lib_suffix}")

    # Skip download if already staged (idempotent across re-configures).
    if(NOT EXISTS "${_lib_file}")
        # Build the archive URL. Naming convention (mirrors setup.py LibDDWaf.get_archive_name): Linux:
        # libddwaf-{ver}-{arch}-linux-musl.tar.gz  ({arch} first) Windows: libddwaf-{ver}-windows-{arch}.tar.gz ({os}
        # first) macOS:   libddwaf-{ver}-darwin-{arch}.tar.gz      ({os} first)
        if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
            set(_archive "libddwaf-${_ddwaf_version}-${_arch}-linux-musl.tar.gz")
        elseif(WIN32)
            set(_archive "libddwaf-${_ddwaf_version}-windows-${_arch}.tar.gz")
        else()
            string(TOLOWER "${CMAKE_SYSTEM_NAME}" _os_lower)
            set(_archive "libddwaf-${_ddwaf_version}-${_os_lower}-${_arch}.tar.gz")
        endif()

        set(_url "${_ddwaf_url_root}/${_ddwaf_version}/${_archive}")
        set(_sha256_url "${_url}.sha256")
        set(_dest "${_ddwaf_cache_dir}/${_archive}")
        set(_sha256_dest "${_dest}.sha256")

        file(MAKE_DIRECTORY "${_ddwaf_cache_dir}")

        if(NOT EXISTS "${_dest}")
            message(STATUS "Downloading libddwaf ${_arch}: ${_url}")
            file(
                DOWNLOAD "${_url}" "${_dest}"
                SHOW_PROGRESS
                STATUS _dl_status)
            list(GET _dl_status 0 _dl_code)
            if(NOT _dl_code EQUAL 0)
                list(GET _dl_status 1 _dl_msg)
                message(FATAL_ERROR "Failed to download ${_url}: ${_dl_msg}")
            endif()

            # Download and verify the SHA256 checksum file.
            file(DOWNLOAD "${_sha256_url}" "${_sha256_dest}" STATUS _sha_status)
            list(GET _sha_status 0 _sha_code)
            if(_sha_code EQUAL 0)
                file(READ "${_sha256_dest}" _sha256_content)
                string(REGEX MATCH "[0-9a-f]+" _expected_hash "${_sha256_content}")
                file(SHA256 "${_dest}" _actual_hash)
                if(NOT _expected_hash STREQUAL _actual_hash)
                    message(FATAL_ERROR "libddwaf checksum mismatch for ${_archive}:\n"
                                        "  expected: ${_expected_hash}\n" "  actual:   ${_actual_hash}")
                endif()
            else()
                message(WARNING "Could not fetch ${_sha256_url} — skipping checksum verification")
            endif()
        else()
            message(STATUS "Using cached libddwaf ${_arch}: ${_dest}")
        endif()

        # Extract only the shared-library file(s) from the tarball.
        file(MAKE_DIRECTORY "${_arch_lib_dir}")
        execute_process(
            COMMAND "${CMAKE_COMMAND}" -E tar xzf "${_dest}"
            WORKING_DIRECTORY "${_ddwaf_cache_dir}"
            RESULT_VARIABLE _tar_result)
        if(NOT _tar_result EQUAL 0)
            message(FATAL_ERROR "Failed to extract ${_dest}")
        endif()

        # The directory name INSIDE the tarball follows {os}-{arch} order for all platforms (mirrors setup.py
        # LibDDWaf.get_package_name): Linux:   libddwaf-{ver}-linux-{arch}/    (no "musl" suffix) Windows:
        # libddwaf-{ver}-windows-{arch}/ macOS:   libddwaf-{ver}-darwin-{arch}/ NOTE: the Linux archive FILENAME uses
        # {arch}-linux-musl, but the directory INSIDE the archive uses linux-{arch}.
        if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
            set(_extracted_dir "${_ddwaf_cache_dir}/libddwaf-${_ddwaf_version}-linux-${_arch}")
        elseif(WIN32)
            set(_extracted_dir "${_ddwaf_cache_dir}/libddwaf-${_ddwaf_version}-windows-${_arch}")
        else()
            string(TOLOWER "${CMAKE_SYSTEM_NAME}" _os_lower)
            set(_extracted_dir "${_ddwaf_cache_dir}/libddwaf-${_ddwaf_version}-${_os_lower}-${_arch}")
        endif()

        # Copy the library, normalising ddwaf.{ext} → libddwaf.{ext}.
        file(GLOB _raw_libs "${_extracted_dir}/lib/ddwaf${_ddwaf_lib_suffix}"
             "${_extracted_dir}/lib/libddwaf${_ddwaf_lib_suffix}")
        foreach(_raw IN LISTS _raw_libs)
            get_filename_component(_raw_name "${_raw}" NAME)
            if(_raw_name MATCHES "^ddwaf")
                file(COPY "${_raw}" DESTINATION "${_arch_lib_dir}")
                file(RENAME "${_arch_lib_dir}/${_raw_name}" "${_lib_file}")
            else()
                file(COPY "${_raw}" DESTINATION "${_arch_lib_dir}")
            endif()
        endforeach()

        file(REMOVE_RECURSE "${_extracted_dir}")
        if(NOT EXISTS "${_lib_file}")
            message(
                FATAL_ERROR
                    "libddwaf staging failed: expected ${_lib_file} after extracting ${_dest}.\n"
                    "The tarball may have a different internal layout. " "Extracted dir searched: ${_extracted_dir}")
        endif()
        message(STATUS "Staged libddwaf/${_arch}")
    endif()

    install(FILES "${_lib_file}" DESTINATION "ddtrace/appsec/_ddwaf/libddwaf/${_arch}/lib")
endforeach()
