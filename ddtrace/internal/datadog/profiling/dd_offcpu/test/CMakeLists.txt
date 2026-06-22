# Test tree for dd_offcpu. The unit tests cover the pure-logic ELF symbolizer
# and only need libelf, so they build and run on a plain CI runner (daemon off).

include(FetchContent)
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

# Mirror dd_wrapper_add_test: build a gtest executable and register it with
# CTest. symbolize.c is compiled as C (its name resolution relies on implicit
# void* conversions) and pulled directly into the test binary alongside the C++
# test, which calls it through extern "C" headers.
function(dd_offcpu_add_test name)
    add_executable(${name} ${ARGN})
    target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../src ${LIBELF_INCLUDE_DIRS})
    target_link_libraries(${name} PRIVATE gtest_main ${LIBELF_LIBRARIES})
    target_link_directories(${name} PRIVATE ${LIBELF_LIBRARY_DIRS})
    gtest_discover_tests(${name})
endfunction()

dd_offcpu_add_test(dd_offcpu_test test_symbolize.cpp ../src/symbolize.c)
