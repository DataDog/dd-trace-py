FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2)
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
    target_include_directories(${name} PRIVATE ../include)
    # this has to refer to the stack_v2 extension name to properly link against
    target_link_libraries(${name} PRIVATE gmock gtest_main ${EXTENSION_NAME})

    if(Python3_LIBRARIES)
        target_link_libraries(${name} PRIVATE ${Python3_LIBRARIES})
    endif()

    set_target_properties(${name} PROPERTIES INSTALL_RPATH "$ORIGIN/../stack_v2")

    add_ddup_config(${name})

    gtest_discover_tests(${name})

    # This is supplemental artifact so make sure to install it in the right place
    if(INPLACE_LIB_INSTALL_DIR)
        set(LIB_INSTALL_DIR "${INPLACE_LIB_INSTALL_DIR}")
    endif()

    if(LIB_INSTALL_DIR)
        install(TARGETS ${name} RUNTIME DESTINATION ${LIB_INSTALL_DIR}/../test)
    endif()
endfunction()

# Add the tests
dd_wrapper_add_test(test_thread_span_links test_thread_span_links.cpp)
