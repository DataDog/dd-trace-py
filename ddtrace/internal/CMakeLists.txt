# ddtrace/internal/CMakeLists.txt Builds ddtrace.internal C/C++ and Cython extensions.
#
# Inherited from top-level: Python3_*           — from find_package(Python3) PYTHON_EXT_SUFFIX   — Python ABI extension
# suffix DD_FAST_BUILD       — option: trade optimisation for build speed DD_CYTHONIZE        — option: compile .pyx
# files CYTHON_EXECUTABLE   — path to cython binary (when DD_CYTHONIZE=ON) _py_version_hex     — Python sys.hexversion
# value

# ── ddtrace.internal._threads ─────────────────────────────────────────────────
if(NOT WIN32)
    python3_add_library(_threads MODULE _threads.cpp)
    target_compile_features(_threads PRIVATE cxx_std_20)
    target_compile_options(_threads PRIVATE -Wall -Wextra $<$<BOOL:${DD_FAST_BUILD}>:-O0>)
    set_target_properties(
        _threads
        PROPERTIES OUTPUT_NAME "_threads"
                   PREFIX ""
                   SUFFIX "${PYTHON_EXT_SUFFIX}")
    install(TARGETS _threads LIBRARY DESTINATION "ddtrace/internal")
endif()

# ── Cython extensions ─────────────────────────────────────────────────────────
if(DD_CYTHONIZE AND CYTHON_EXECUTABLE)
    # Endian detection — required by _encoding to select pack byte order.
    include(TestBigEndian)
    test_big_endian(_is_big_endian)
    if(_is_big_endian)
        set(_endian_define "__BIG_ENDIAN__=1")
    else()
        set(_endian_define "__LITTLE_ENDIAN__=1")
    endif()

    dd_add_cython_ext(cython_tagset "ddtrace/internal" PYX ddtrace/internal/_tagset.pyx)

    # _encoding needs the repo root and ddtrace/internal on the include path so pack.h / pack_template.h are found.
    dd_add_cython_ext(
        cython_encoding
        "ddtrace/internal"
        PYX
        ddtrace/internal/_encoding.pyx
        DEFS
        "${_endian_define}"
        LIBS
        $<$<PLATFORM_ID:Windows>:ws2_32>)
    target_include_directories(cython_encoding PRIVATE "${CMAKE_SOURCE_DIR}" "${CMAKE_SOURCE_DIR}/ddtrace/internal")

    dd_add_cython_ext(cython_metrics_namespaces "ddtrace/internal/telemetry" PYX
                      ddtrace/internal/telemetry/metrics_namespaces.pyx)
endif()
