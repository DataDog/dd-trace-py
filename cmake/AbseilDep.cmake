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
    # once the initial clone is in the cache (set by FETCHCONTENT_BASE_DIR in setup.py).
    set(FETCHCONTENT_UPDATES_DISCONNECTED
        ON
        CACHE BOOL "" FORCE)
    FetchContent_Declare(
        absl
        GIT_REPOSITORY https://github.com/abseil/abseil-cpp.git
        GIT_TAG 20250127.1
        GIT_SHALLOW TRUE
        GIT_PROGRESS TRUE)
    FetchContent_MakeAvailable(absl)
endif()
