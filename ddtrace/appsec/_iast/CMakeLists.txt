# ddtrace/appsec/_iast/CMakeLists.txt Builds the three IAST C/C++ extensions: _stacktrace, iastpatch, iast_native.
#
# Inherited from top-level: Python3_*           — from find_package(Python3) PYTHON_EXT_SUFFIX   — Python ABI extension
# suffix DD_FAST_BUILD       — option: trade optimisation for build speed absl::*             — Abseil targets (from
# FetchContent in top-level)

if(WIN32)
    return()
endif()

# ── ddtrace.appsec._iast._stacktrace ─────────────────────────────────────────
python3_add_library(_stacktrace MODULE "${CMAKE_SOURCE_DIR}/ddtrace/appsec/_iast/_stacktrace.c")
target_compile_definitions(_stacktrace PRIVATE Py_BUILD_CORE)
target_compile_options(_stacktrace PRIVATE $<$<CONFIG:Debug>:-g -O0 -Wall -Wextra -Wpedantic>
                                           $<$<BOOL:${DD_FAST_BUILD}>:-O0>)
set_target_properties(
    _stacktrace
    PROPERTIES OUTPUT_NAME "_stacktrace"
               PREFIX ""
               SUFFIX "${PYTHON_EXT_SUFFIX}")
install(TARGETS _stacktrace LIBRARY DESTINATION "ddtrace/appsec/_iast")

# ── ddtrace.appsec._iast._ast.iastpatch ──────────────────────────────────────
python3_add_library(iastpatch MODULE "${CMAKE_SOURCE_DIR}/ddtrace/appsec/_iast/_ast/iastpatch.c")
target_compile_definitions(iastpatch PRIVATE Py_BUILD_CORE)
target_compile_options(iastpatch PRIVATE $<$<CONFIG:Debug>:-g -O0 -Wall -Wextra -Wpedantic>
                                         $<$<BOOL:${DD_FAST_BUILD}>:-O0>)
set_target_properties(
    iastpatch
    PROPERTIES OUTPUT_NAME "iastpatch"
               PREFIX ""
               SUFFIX "${PYTHON_EXT_SUFFIX}")
install(TARGETS iastpatch LIBRARY DESTINATION "ddtrace/appsec/_iast/_ast")

# ── ddtrace.appsec._iast._taint_tracking._native ─────────────────────────────
set(_iast_dir "${CMAKE_CURRENT_SOURCE_DIR}/_taint_tracking")

# Collect all .cpp sources from root + the 7 sub-directories.
file(
    GLOB
    _iast_sources
    "${_iast_dir}/*.cpp"
    "${_iast_dir}/api/*.cpp"
    "${_iast_dir}/aspects/*.cpp"
    "${_iast_dir}/context/*.cpp"
    "${_iast_dir}/initializer/*.cpp"
    "${_iast_dir}/taint_tracking/*.cpp"
    "${_iast_dir}/tainted_ops/*.cpp"
    "${_iast_dir}/utils/*.cpp")

python3_add_library(iast_native MODULE ${_iast_sources})
target_compile_features(iast_native PRIVATE cxx_std_17)
target_compile_options(
    iast_native
    PRIVATE -fPIC
            -fexceptions
            -fvisibility=hidden
            -fpermissive
            -pthread
            -Wall
            -Wno-unknown-pragmas
            -U_FORTIFY_SOURCE
            -g
            $<$<BOOL:${DD_FAST_BUILD}>:-O0>)
target_include_directories(iast_native PRIVATE "${_iast_dir}" "${_iast_dir}/_vendor/pybind11/include"
                                               ${Python3_INCLUDE_DIRS})
# Abseil — targets created by FetchContent_MakeAvailable(absl) in the top-level.
target_link_libraries(iast_native PRIVATE absl::base absl::flat_hash_map absl::node_hash_map absl::strings
                                          absl::synchronization)
set_target_properties(
    iast_native
    PROPERTIES OUTPUT_NAME "_native"
               PREFIX ""
               SUFFIX "${PYTHON_EXT_SUFFIX}")
install(TARGETS iast_native LIBRARY DESTINATION "ddtrace/appsec/_iast/_taint_tracking")
