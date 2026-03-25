# ProfExtensionHelpers.cmake Shared CMake helpers for ddtrace profiling extensions (dd_wrapper, _ddup, _stack). Include
# from the extension's CMakeLists.txt:
#
# list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake") include(ProfExtensionHelpers)
#
# ─────────────────────────────────────────────────────────────────────────────

# dd_import_dd_wrapper()
#
# Creates a SHARED IMPORTED target for dd_wrapper when building an extension outside the top-level scikit-build-core
# tree (e.g., standalone or test builds). When building inside the top-level tree, dd_wrapper is already a real built
# target — the guard prevents creating a conflicting IMPORTED target.
#
# Resolution order: 1. ${LIB_INSTALL_DIR}/libdd_wrapper${EXTENSION_SUFFIX}  (if LIB_INSTALL_DIR is set) 2.
# ${NATIVE_EXTENSION_LOCATION}/../datadog/profiling/libdd_wrapper${EXTENSION_SUFFIX}
#
macro(dd_import_dd_wrapper)
    if(NOT TARGET dd_wrapper)
        if(LIB_INSTALL_DIR AND EXISTS "${LIB_INSTALL_DIR}/libdd_wrapper${EXTENSION_SUFFIX}")
            set(_dd_wrapper_lib "${LIB_INSTALL_DIR}/libdd_wrapper${EXTENSION_SUFFIX}")
        else()
            set(_dd_wrapper_lib "${NATIVE_EXTENSION_LOCATION}/../datadog/profiling/libdd_wrapper${EXTENSION_SUFFIX}")
        endif()
        add_library(dd_wrapper SHARED IMPORTED)
        set_target_properties(
            dd_wrapper PROPERTIES IMPORTED_LOCATION "${_dd_wrapper_lib}"
                                  INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}/../dd_wrapper/include")
        unset(_dd_wrapper_lib)
    endif()
endmacro()

# dd_install_if_dir(target)
#
# Installs <target>'s library/archive/runtime artefacts to LIB_INSTALL_DIR when that variable is set.  No-op otherwise
# (wheel builds use scikit-build-core's install() rules placed elsewhere).
#
function(dd_install_if_dir target)
    if(LIB_INSTALL_DIR)
        install(
            TARGETS ${target}
            LIBRARY DESTINATION ${LIB_INSTALL_DIR}
            ARCHIVE DESTINATION ${LIB_INSTALL_DIR}
            RUNTIME DESTINATION ${LIB_INSTALL_DIR})
    endif()
endfunction()

# dd_finalize_profiling_extension(target)
#
# Applies the standard finishing touches to a profiling Python extension target:
#
# 1. Strips the CMake-added lib prefix and default suffix (Python import requires exact filename control).
# 2. Sets INSTALL_RPATH / BUILD_WITH_INSTALL_RPATH so the extension finds libdd_wrapper at runtime without relying on
#    LD_LIBRARY_PATH / DYLD_:. The RPATH points one directory up ("..") from the extension's install location, which is
#    where dd_wrapper lives in both the wheel layout and editable installs.  For standalone / test builds that set
#    LIB_INSTALL_DIR the actual install path is appended so tests can also resolve the library.
# 3. Calls dd_install_if_dir() to register the install rule.
#
function(dd_finalize_profiling_extension target)
    # Strip lib prefix and default .so/.dylib suffix so Python can import the module.
    set_target_properties(${target} PROPERTIES PREFIX "" SUFFIX "")

    # RPATH — use BUILD_WITH_INSTALL_RPATH so the rpath is baked in at link time and CMake's install step does not strip
    # or re-set it.
    if(APPLE)
        if(BUILD_TESTING AND LIB_INSTALL_DIR)
            set_target_properties(${target} PROPERTIES INSTALL_RPATH "@loader_path/..;${LIB_INSTALL_DIR}"
                                                       BUILD_WITH_INSTALL_RPATH TRUE)
        else()
            set_target_properties(${target} PROPERTIES INSTALL_RPATH "@loader_path/.." BUILD_WITH_INSTALL_RPATH TRUE)
        endif()
    elseif(UNIX)
        if(BUILD_TESTING AND LIB_INSTALL_DIR)
            set_target_properties(${target} PROPERTIES INSTALL_RPATH "$ORIGIN/..;${LIB_INSTALL_DIR}"
                                                       BUILD_WITH_INSTALL_RPATH TRUE)
        else()
            set_target_properties(${target} PROPERTIES INSTALL_RPATH "$ORIGIN/.." BUILD_WITH_INSTALL_RPATH TRUE)
        endif()
    endif()

    dd_install_if_dir(${target})
endfunction()
