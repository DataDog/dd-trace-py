# CythonHelpers.cmake Defines dd_add_cython_ext(), a helper for building a .pyx file into a Python C extension.  Include
# once from the top-level CMakeLists.txt; the function is then available in all add_subdirectory() scopes.
#
# Prerequisites (must be set before calling the function): CYTHON_EXECUTABLE  — path to the cython binary (find_program
# in top-level) _py_version_hex    — Python sys.hexversion value (computed in top-level) PYTHON_EXT_SUFFIX  — Python ABI
# suffix, e.g. ".cpython-311-x86_64-linux-gnu.so"
#
# Usage: dd_add_cython_ext(<cmake-target> <install-dest> PYX     <path/to/file.pyx>    # relative to CMAKE_SOURCE_DIR
# [DEFS   KEY=VALUE ...]        # extra target_compile_definitions [LIBS   lib1 lib2 ...]        # extra
# target_link_libraries )

function(dd_add_cython_ext target install_dest)
    cmake_parse_arguments(ARG "" "PYX" "DEFS;LIBS" ${ARGN})

    get_filename_component(_base "${ARG_PYX}" NAME_WE)
    get_filename_component(_rel_dir "${ARG_PYX}" DIRECTORY)

    # Generated .c file lives in the cmake binary tree to avoid polluting the source tree.
    set(_c_out "${CMAKE_BINARY_DIR}/cython/${_rel_dir}/${_base}.c")

    # Create parent dir for the generated file at configure time.
    get_filename_component(_c_out_dir "${_c_out}" DIRECTORY)
    file(MAKE_DIRECTORY "${_c_out_dir}")

    # Cython --depfile emits a Makefile-format .dep file listing all .pxd includes transitively.  CMake's DEPFILE reads
    # it so Ninja re-runs cythonize whenever any included .pxd changes, including cross-directory dependencies — no
    # file(GLOB) needed.
    set(_dep_out "${_c_out}.dep")

    add_custom_command(
        OUTPUT "${_c_out}"
        COMMAND
            "${CYTHON_EXECUTABLE}" --directive language_level=3 "-E" "PY_MAJOR_VERSION=${Python3_VERSION_MAJOR}" "-E"
            "PY_MINOR_VERSION=${Python3_VERSION_MINOR}" "-E" "PY_MICRO_VERSION=${Python3_VERSION_PATCH}" "-E"
            "PY_VERSION_HEX=${_py_version_hex}" --depfile -o "${_c_out}" "${CMAKE_SOURCE_DIR}/${ARG_PYX}"
        DEPENDS "${CMAKE_SOURCE_DIR}/${ARG_PYX}"
        DEPFILE "${_dep_out}"
        COMMENT "Cythonizing ${ARG_PYX}"
        VERBATIM)

    python3_add_library(${target} MODULE "${_c_out}")

    if(ARG_DEFS)
        target_compile_definitions(${target} PRIVATE ${ARG_DEFS})
    endif()
    if(ARG_LIBS)
        target_link_libraries(${target} PRIVATE ${ARG_LIBS})
    endif()

    set_target_properties(
        ${target}
        PROPERTIES OUTPUT_NAME "${_base}"
                   PREFIX ""
                   SUFFIX "${PYTHON_EXT_SUFFIX}")

    install(TARGETS ${target} LIBRARY DESTINATION "${install_dest}")
endfunction()
