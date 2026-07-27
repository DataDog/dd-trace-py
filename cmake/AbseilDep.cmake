# AbseilDep.cmake — resolve abseil via pre-built install, FetchContent, or skip.
#
# Consuming CMakeLists.txt files should do:
#
# include(AbseilDep)
#
# After inclusion, absl:: targets are available (or DONT_COMPILE_ABSEIL is defined if abseil was disabled/skipped).
#
# Variables consulted: ABSL_INSTALL_DIR   — path to a pre-built abseil install tree (set by setup.py) DD_COMPILE_ABSEIL
# — env var; "0" or "false" disables abseil entirely CMAKE_BUILD_TYPE   — "Debug" also disables abseil

include(FetchContent)

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
elseif(DEFINED ABSL_INSTALL_DIR)
    # setup.py pre-built abseil once via cmake/abseil/CMakeLists.txt and installed it to ABSL_INSTALL_DIR.  Use
    # find_package() so we link against the existing build instead of compiling abseil a second time inside this
    # project.
    message(STATUS "Using pre-built abseil from ${ABSL_INSTALL_DIR}")
    find_package(absl CONFIG REQUIRED PATHS "${ABSL_INSTALL_DIR}" NO_DEFAULT_PATH)
else()
    message(STATUS "Release/RelWithDebInfo/MinSizeRel mode: fetching and building abseil")
    # Use a git-based fetch rather than a ZIP download: git shallow clones are more resilient to GitHub transient
    # failures than release archive downloads. FETCHCONTENT_UPDATES_DISCONNECTED prevents re-fetching on every configure
    # once the initial clone is in the cache (set by FETCHCONTENT_BASE_DIR in setup.py). Resolve FETCHCONTENT_BASE_DIR
    # so we know where to clone abseil.
    if(NOT DEFINED FETCHCONTENT_BASE_DIR)
        set(FETCHCONTENT_BASE_DIR "${CMAKE_BINARY_DIR}/_deps")
    endif()
    set(_absl_src_dir "${FETCHCONTENT_BASE_DIR}/absl-src")
    set(_absl_bin_dir "${FETCHCONTENT_BASE_DIR}/absl-build")

    # Immutable commit SHA backing tag 20250127.1. We clone by tag (shallow clones by tag are resilient to GitHub
    # transient failures) but then verify the checked-out commit matches this SHA, so a retagged or compromised upstream
    # tag fails the build instead of injecting code.
    set(_absl_tag "20250127.1")
    set(_absl_expected_sha "d9e4955c65cd4367dd6bf46f4ccb8cd3d100540b")

    # Only clone if the source directory is not already present (disconnected / cached build support — equivalent to
    # FETCHCONTENT_UPDATES_DISCONNECTED).
    if(NOT EXISTS "${_absl_src_dir}/.git")
        set(_absl_max_attempts 3)
        set(_absl_attempt 0)
        set(_absl_success FALSE)
        while(NOT _absl_success AND _absl_attempt LESS _absl_max_attempts)
            math(EXPR _absl_attempt "${_absl_attempt} + 1")
            message(STATUS "Cloning abseil (attempt ${_absl_attempt}/${_absl_max_attempts})...")
            execute_process(
                COMMAND git clone --depth 1 --branch ${_absl_tag} --progress https://github.com/abseil/abseil-cpp.git
                        "${_absl_src_dir}" RESULT_VARIABLE _absl_result)
            if(_absl_result EQUAL 0)
                set(_absl_success TRUE)
            elseif(_absl_attempt LESS _absl_max_attempts)
                message(WARNING "Abseil clone attempt ${_absl_attempt} failed (exit ${_absl_result}), retrying...")
                file(REMOVE_RECURSE "${_absl_src_dir}")
            else()
                message(FATAL_ERROR "Failed to clone abseil after ${_absl_max_attempts} attempts.")
            endif()
        endwhile()
    else()
        message(STATUS "Using cached abseil source at ${_absl_src_dir}")
    endif()

    # Verify the resolved source matches the expected immutable commit (covers both a fresh clone by tag and a
    # cached/tampered source tree) before building it.
    execute_process(
        COMMAND git -C "${_absl_src_dir}" rev-parse HEAD
        OUTPUT_VARIABLE _absl_actual_sha
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _absl_revparse_result)
    if(NOT _absl_revparse_result EQUAL 0)
        message(FATAL_ERROR "Unable to determine abseil commit at ${_absl_src_dir} for integrity verification.")
    endif()
    if(NOT _absl_actual_sha STREQUAL _absl_expected_sha)
        message(FATAL_ERROR "Abseil integrity check failed: expected commit ${_absl_expected_sha} (tag ${_absl_tag}) "
                            "but found ${_absl_actual_sha}. Refusing to build a potentially tampered dependency.")
    endif()

    add_subdirectory("${_absl_src_dir}" "${_absl_bin_dir}" EXCLUDE_FROM_ALL)
endif()
