FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG release-1.11.0)
set(gtest_force_shared_crt
    ON
    CACHE BOOL "" FORCE)
set(INSTALL_GTEST
    OFF
    CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)
include(GoogleTest)
include(AnalysisFunc)

function(dd_wrapper_add_test name)
    add_executable(${name} ${ARGN})
    target_include_directories(${name} PRIVATE ../include)
    target_link_libraries(${name} PRIVATE gmock gtest_main _stack_v2)
    add_ddup_config(${name})

    gtest_discover_tests(${name})
endfunction()

# Add the tests
dd_wrapper_add_test(thread_span_links thread_span_links.cpp)
