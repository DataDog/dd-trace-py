# CMakeLists.txt for vendored psutil C extensions.
#
# This is a direct translation of the platform-conditional logic in ddtrace/vendor/psutil/setup.py::get_extensions().
# The extensions are installed into ddtrace/vendor/psutil/ inside the wheel so that ddtrace.vendor.psutil can import
# them via relative import.
#
# All extensions are skipped on Pyston (no C extensions) and on any platform not listed below; cmake will emit a STATUS
# message and continue rather than failing the whole build.

cmake_minimum_required(VERSION 3.24)
project(ddtrace_psutil LANGUAGES C)

find_package(
    Python3
    COMPONENTS Interpreter Development.Module
    REQUIRED)

# ── Version ─────────────────────────────────────────────────────────────────── Extract __version__ from __init__.py
# without importing it.
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/__init__.py" _psutil_init)
string(REGEX MATCH "__version__ = \"([0-9]+\\.[0-9]+\\.[0-9]+)\"" _ "${_psutil_init}")
set(PSUTIL_VERSION "${CMAKE_MATCH_1}")
if(NOT PSUTIL_VERSION)
    message(FATAL_ERROR "Could not determine psutil version from __init__.py")
endif()
string(REPLACE "." "" PSUTIL_VERSION_INT "${PSUTIL_VERSION}")
message(STATUS "psutil version: ${PSUTIL_VERSION}")

# ── Extension suffix ────────────────────────────────────────────────────────── Inherit from parent if already set,
# otherwise derive from Python.
if(NOT DEFINED PYTHON_EXT_SUFFIX)
    execute_process(
        COMMAND "${Python3_EXECUTABLE}" -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'), end='')"
        OUTPUT_VARIABLE PYTHON_EXT_SUFFIX COMMAND_ERROR_IS_FATAL ANY)
endif()

# ── Common macros ─────────────────────────────────────────────────────────────
set(_psutil_defs PSUTIL_VERSION=${PSUTIL_VERSION_INT})

# POSIX: used by arch/posix/ and the platform-specific extension.
if(UNIX)
    list(APPEND _psutil_defs PSUTIL_POSIX=1)
endif()

# BSD umbrella.
if(CMAKE_SYSTEM_NAME MATCHES "FreeBSD|OpenBSD|NetBSD|DragonFly")
    list(APPEND _psutil_defs PSUTIL_BSD=1)
endif()

# sizeof(pid_t): psutil uses this to define _Py_PARSE_PID when it's missing. 4 if long ≤ 8 bytes, otherwise 8 (matches
# psutil's struct.calcsize logic).
if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    list(APPEND _psutil_defs PSUTIL_SIZEOF_PID_T=4)
else()
    list(APPEND _psutil_defs PSUTIL_SIZEOF_PID_T=4)
endif()

# ── Common sources ────────────────────────────────────────────────────────────
file(GLOB _arch_all_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/all/*.c")
file(GLOB _arch_posix_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/posix/*.c")

# ── Helper: create one psutil extension target ──────────────────────────────── Usage: _psutil_add_ext(<target>
# <output_name> <extra_sources...>) Common sources, defs, and install rule are applied automatically.
function(_psutil_add_ext target output_name)
    set(_extra_sources ${ARGN})

    python3_add_library(${target} MODULE ${_arch_all_sources} ${_extra_sources})

    target_compile_definitions(${target} PRIVATE ${_psutil_defs} ${_extra_defs})
    target_include_directories(${target} PRIVATE ${Python3_INCLUDE_DIRS})

    set_target_properties(
        ${target}
        PROPERTIES OUTPUT_NAME "${output_name}"
                   PREFIX ""
                   SUFFIX "${PYTHON_EXT_SUFFIX}")

    install(TARGETS ${target} LIBRARY DESTINATION "ddtrace/vendor/psutil")
endfunction()

# ── Platform-specific extension ───────────────────────────────────────────────

if(WIN32)
    # ── Windows ───────────────────────────────────────────────────────────────
    list(APPEND _psutil_defs PSUTIL_WINDOWS=1)

    # _WIN32_WINNT / _AVAIL_WINVER_: mirror psutil's setup.py get_winver() which computes (major * 100 + minor) and
    # formats it as "0x0<decimal>". e.g. Windows 10.0 → (10*100)+0 = 1000 → "0x01000" (> 0x0601), which prevents
    # ntextapi.h from redefining types already in the Windows 10 SDK. cmake's CMAKE_SYSTEM_VERSION is
    # "Major.Minor.Build" on Windows.
    string(REGEX MATCH "^([0-9]+)\\.([0-9]+)" _winver_match "${CMAKE_SYSTEM_VERSION}")
    if(_winver_match)
        set(_win_major "${CMAKE_MATCH_1}")
        set(_win_minor "${CMAKE_MATCH_2}")
        math(EXPR _winver_int "(${_win_major} * 100) + ${_win_minor}")
        set(_winver "0x0${_winver_int}")
    else()
        set(_winver "0x0601") # Windows 7 floor — avoids ntextapi.h redefinitions
    endif()
    list(APPEND _psutil_defs _WIN32_WINNT=${_winver} _AVAIL_WINVER_=${_winver} _CRT_SECURE_NO_WARNINGS PSAPI_VERSION=1)

    file(GLOB _win_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/windows/*.c")
    _psutil_add_ext(psutil_windows _psutil_windows "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_windows.c" ${_win_sources})
    target_link_libraries(
        psutil_windows
        PRIVATE advapi32
                kernel32
                netapi32
                pdh
                PowrProf
                psapi
                shell32
                ws2_32)

elseif(APPLE)
    # ── macOS ─────────────────────────────────────────────────────────────────
    list(APPEND _psutil_defs PSUTIL_OSX=1 PSUTIL_MACOS=1)

    file(GLOB _osx_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/osx/*.c")
    _psutil_add_ext(psutil_osx _psutil_osx "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_osx.c" ${_arch_posix_sources}
                    ${_osx_sources})
    find_library(_psutil_CoreFoundation CoreFoundation REQUIRED)
    find_library(_psutil_IOKit IOKit REQUIRED)
    target_link_libraries(psutil_osx PRIVATE "${_psutil_CoreFoundation}" "${_psutil_IOKit}")

elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # ── Linux ─────────────────────────────────────────────────────────────────
    list(APPEND _psutil_defs PSUTIL_LINUX=1)

    # Detect missing ethtool types (mirrors unix_can_compile check in setup.py).
    include(CheckIncludeFile)
    check_include_file("linux/ethtool.h" HAVE_ETHTOOL_H)
    if(NOT HAVE_ETHTOOL_H)
        list(APPEND _psutil_defs PSUTIL_ETHTOOL_MISSING_TYPES=1)
    endif()

    file(GLOB _linux_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/linux/*.c")
    _psutil_add_ext(psutil_linux _psutil_linux "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_linux.c" ${_arch_posix_sources}
                    ${_linux_sources})

elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    list(APPEND _psutil_defs PSUTIL_FREEBSD=1)
    file(GLOB _bsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/bsd/*.c")
    file(GLOB _fbsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/freebsd/*.c")
    _psutil_add_ext(psutil_bsd _psutil_bsd "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_bsd.c" ${_arch_posix_sources}
                    ${_bsd_sources} ${_fbsd_sources})
    target_link_libraries(psutil_bsd PRIVATE devstat)

elseif(CMAKE_SYSTEM_NAME STREQUAL "OpenBSD")
    list(APPEND _psutil_defs PSUTIL_OPENBSD=1)
    file(GLOB _bsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/bsd/*.c")
    file(GLOB _obsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/openbsd/*.c")
    _psutil_add_ext(psutil_bsd _psutil_bsd "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_bsd.c" ${_arch_posix_sources}
                    ${_bsd_sources} ${_obsd_sources})
    target_link_libraries(psutil_bsd PRIVATE kvm)

elseif(CMAKE_SYSTEM_NAME STREQUAL "NetBSD")
    list(APPEND _psutil_defs PSUTIL_NETBSD=1)
    file(GLOB _bsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/bsd/*.c")
    file(GLOB _nbsd_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/netbsd/*.c")
    _psutil_add_ext(psutil_bsd _psutil_bsd "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_bsd.c" ${_arch_posix_sources}
                    ${_bsd_sources} ${_nbsd_sources})
    target_link_libraries(psutil_bsd PRIVATE kvm)

elseif(CMAKE_SYSTEM_NAME STREQUAL "SunOS")
    list(APPEND _psutil_defs PSUTIL_SUNOS=1)
    file(GLOB _sunos_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/sunos/*.c")
    _psutil_add_ext(psutil_sunos _psutil_sunos "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_sunos.c" ${_arch_posix_sources}
                    ${_sunos_sources})
    target_link_libraries(psutil_sunos PRIVATE kstat nsl socket)

elseif(CMAKE_SYSTEM_NAME STREQUAL "AIX")
    list(APPEND _psutil_defs PSUTIL_AIX=1)
    file(GLOB _aix_sources "${CMAKE_CURRENT_SOURCE_DIR}/arch/aix/*.c")
    _psutil_add_ext(psutil_aix _psutil_aix "${CMAKE_CURRENT_SOURCE_DIR}/_psutil_aix.c" ${_arch_posix_sources}
                    ${_aix_sources})
    target_link_libraries(psutil_aix PRIVATE perfstat)

else()
    message(STATUS "psutil: platform ${CMAKE_SYSTEM_NAME} is not supported, skipping C extensions")
endif()

# ── _psutil_posix (POSIX-only shared helper) ────────────────────────────────── On all non-Windows platforms psutil
# also builds a small _psutil_posix extension from the arch/posix/ sources.  SunOS additionally needs -lsocket.
if(UNIX AND NOT APPLE)
    # macOS doesn't build a separate _psutil_posix — it's merged into _psutil_osx.
    python3_add_library(psutil_posix MODULE ${_arch_posix_sources})
    target_compile_definitions(psutil_posix PRIVATE ${_psutil_defs})
    target_include_directories(psutil_posix PRIVATE ${Python3_INCLUDE_DIRS})
    set_target_properties(
        psutil_posix
        PROPERTIES OUTPUT_NAME "_psutil_posix"
                   PREFIX ""
                   SUFFIX "${PYTHON_EXT_SUFFIX}")
    if(CMAKE_SYSTEM_NAME STREQUAL "SunOS")
        target_link_libraries(psutil_posix PRIVATE socket)
    endif()
    install(TARGETS psutil_posix LIBRARY DESTINATION "ddtrace/vendor/psutil")
endif()
