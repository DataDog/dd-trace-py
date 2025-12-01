cmake_minimum_required(VERSION 3.10)
project(test_memalloc_heap_map)

include(FetchContent)
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2
    TIMEOUT 180
    INACTIVITY_TIMEOUT 120)
set(gtest_force_shared_crt
    ON
    CACHE BOOL "" FORCE)
set(INSTALL_GTEST
    OFF
    CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)
include(GoogleTest)

find_package(
    Python3
    COMPONENTS Interpreter Development
    REQUIRED)

# Add the test executable
add_executable(test_memalloc_heap_map test_memalloc_heap_map.cpp)

# Include directories
target_include_directories(test_memalloc_heap_map PRIVATE .. ${Python3_INCLUDE_DIRS}
                                                          ${CMAKE_CURRENT_SOURCE_DIR}/../vendor)

# Link libraries
target_link_libraries(test_memalloc_heap_map PRIVATE gtest gtest_main ${Python3_LIBRARIES})

# Add source files that need to be compiled
target_sources(test_memalloc_heap_map PRIVATE ../_memalloc_heap_map.cpp ../_memalloc_tb.cpp ../_memalloc_reentrant.cpp)

# Python library linking
if(Python3_LIBRARY)
    target_link_libraries(test_memalloc_heap_map PRIVATE ${Python3_LIBRARY})
endif()

# Compiler flags
target_compile_definitions(test_memalloc_heap_map PRIVATE PY_SSIZE_T_CLEAN)

# Discover and register tests
gtest_discover_tests(test_memalloc_heap_map)
