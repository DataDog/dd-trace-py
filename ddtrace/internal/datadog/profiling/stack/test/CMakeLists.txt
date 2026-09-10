set(FETCHCONTENT_UPDATES_DISCONNECTED
    ON
    CACHE BOOL "" FORCE)
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2
    GIT_SHALLOW TRUE
    GIT_PROGRESS TRUE)
set(gtest_force_shared_crt
    ON
    CACHE BOOL "" FORCE)
set(INSTALL_GTEST
    OFF
    CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)
include(GoogleTest)
include(AnalysisFunc)

if(DO_VALGRIND)
    find_program(
        VALGRIND_EXECUTABLE
        NAMES valgrind
        PATHS /usr/bin /usr/local/bin)

    if(VALGRIND_EXECUTABLE)
        set(MEMORYCHECK_COMMAND "${VALGRIND_EXECUTABLE}")
        set(MEMORYCHECK_COMMAND_OPTIONS
            "--leak-check=full --show-leak-kinds=definite --errors-for-leak-kinds=definite --trace-children=yes --error-exitcode=1 --log-fd=1 --suppressions=${CMAKE_CURRENT_SOURCE_DIR}/valgrind.supp"
        )
    else()
        message(FATAL_ERROR "Valgrind not found")
    endif()

    include(CTest)
endif()

function(dd_wrapper_add_test name)
    add_executable(${name} ${ARGN})
    # Replicate the include dirs that ${EXTENSION_NAME} sets PRIVATE (so they don't propagate to test targets): stack's
    # own headers, the profiling root (for "dd_wrapper/include/sample.hpp"), Python, echion, and the libdatadog
    # Rust-generated headers.
    target_include_directories(${name} PRIVATE ../include ../..)
    target_include_directories(
        ${name} SYSTEM
        PRIVATE ${Python3_INCLUDE_DIRS} ../echion
                ../../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include)
    # this has to refer to the stack extension name to properly link against
    target_link_libraries(${name} PRIVATE gmock gtest_main ${EXTENSION_NAME})

    if(Python3_LIBRARIES)
        target_link_libraries(${name} PRIVATE ${Python3_LIBRARIES})
    endif()

    add_ddup_config(${name})

    # Test executable needs to find _stack in parent directory Append to INSTALL_RPATH instead of replacing to preserve
    # sanitizer library paths set by add_ddup_config
    if(APPLE)
        set(_rpath_prefix "@loader_path")
    else()
        set(_rpath_prefix "$ORIGIN")
    endif()

    get_target_property(EXISTING_INSTALL_RPATH ${name} INSTALL_RPATH)
    if(EXISTING_INSTALL_RPATH)
        set_target_properties(${name} PROPERTIES INSTALL_RPATH "${EXISTING_INSTALL_RPATH};${_rpath_prefix}/.."
                                                 BUILD_WITH_INSTALL_RPATH TRUE)
    else()
        set_target_properties(${name} PROPERTIES INSTALL_RPATH "${_rpath_prefix}/.." BUILD_WITH_INSTALL_RPATH TRUE)
    endif()

    gtest_discover_tests(${name} DISCOVERY_MODE PRE_TEST) # Delay test discovery until test execution to avoid running
                                                          # sanitizer-built executables during build

    # Echion headers (vm.h, tasks.h) require PL_DARWIN or PL_LINUX to define proc_ref_t and copy_type. These are set
    # PRIVATE on ${EXTENSION_NAME} and don't propagate to test targets, so we replicate the logic here.
    if(APPLE)
        target_compile_definitions(${name} PRIVATE PL_DARWIN)
    else()
        target_compile_definitions(${name} PRIVATE PL_LINUX)
    endif()
endfunction()

# Add the tests
dd_wrapper_add_test(test_thread_span_links test_thread_span_links.cpp)
dd_wrapper_add_test(test_origin_task_links test_origin_task_links.cpp)

function(configure_stack_internal_test name)
    target_include_directories(${name} PRIVATE ../..)
    target_include_directories(
        ${name} SYSTEM
        PRIVATE ${Python3_INCLUDE_DIRS} ../echion ../include/vendored ../include/util
                ../../../../../../src/native/target${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}/include)
    target_compile_definitions(${name} PRIVATE "$<TARGET_PROPERTY:${EXTENSION_NAME},COMPILE_DEFINITIONS>")
    if(LIB_INSTALL_DIR)
        set_property(
            TARGET ${name}
            APPEND
            PROPERTY INSTALL_RPATH "${LIB_INSTALL_DIR}")
    endif()
endfunction()

dd_wrapper_add_test(test_sample_lifecycle test_sample_lifecycle.cpp)
target_link_libraries(test_sample_lifecycle PRIVATE dd_wrapper)
configure_stack_internal_test(test_sample_lifecycle)

dd_wrapper_add_test(test_sampling_cycle_state test_sampling_cycle_state.cpp)
configure_stack_internal_test(test_sampling_cycle_state)

dd_wrapper_add_test(test_asyncio_debug test_asyncio_debug.cpp)
configure_stack_internal_test(test_asyncio_debug)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # Exercise /proc/self/exe identity validation when the executable has no GNU build ID.
    target_link_options(test_asyncio_debug PRIVATE -Wl,--build-id=none -Wl,--undefined=process_asyncio_debug_offsets)
endif()

dd_wrapper_add_test(test_task_traversal test_task_traversal.cpp)
configure_stack_internal_test(test_task_traversal)

dd_wrapper_add_test(test_alt_stack_ownership test_alt_stack_ownership.cpp)
# ThreadAltStack lives in the vendored echion header tree.
target_include_directories(test_alt_stack_ownership PRIVATE ../echion)

# vm.cc is compiled into the test binary: the fast-copy state lives in inline variables, so the copy in the test
# executable is distinct from the one in the _stack library and only the constructor linked here initializes it.
dd_wrapper_add_test(test_embedded_fast_copy test_embedded_fast_copy.cpp ../src/echion/vm.cc)
configure_stack_internal_test(test_embedded_fast_copy)
# test_frame_state_315 validates the PyFrameState renumbering and related frame-internals changes introduced in Python
# 3.15. All test bodies are gated on PY_VERSION_HEX >= 0x030f0000, so building on older versions would produce an empty
# test binary.
if(Python3_VERSION VERSION_GREATER_EQUAL "3.15")
    dd_wrapper_add_test(test_frame_state_315 test_frame_state_315.cpp)
    # Route copy_memory through echion_fuzz_copy_memory so the tests can drive it with a buffer-backed fake process
    # image (fuzz/fuzz_memory_image.h) and observe how far PyGen_yf got.
    target_compile_definitions(test_frame_state_315 PRIVATE ECHION_FUZZING)
    target_include_directories(test_frame_state_315 PRIVATE ../fuzz)
endif()
