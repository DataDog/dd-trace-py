cmake_minimum_required(VERSION 3.19)

# Download and configure Google Test
include(FetchContent)
FetchContent_Declare(googletest URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip)
# For Windows: Prevent overriding the parent project's compiler/linker settings
set(gtest_force_shared_crt
    ON
    CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

# Find Python
find_package(
    Python3
    COMPONENTS Interpreter Development
    REQUIRED)

# Test executable for heap map
add_executable(test_memalloc_heap_map ../_memalloc_heap_map.cpp ../_memalloc_tb.cpp test_memalloc_heap_map.cpp)

target_include_directories(test_memalloc_heap_map PRIVATE ${Python3_INCLUDE_DIRS} ${CMAKE_CURRENT_SOURCE_DIR}/..)

target_link_libraries(test_memalloc_heap_map PRIVATE GTest::gtest_main Python3::Python)

# Link Abseil if available
if(NOT (CMAKE_BUILD_TYPE STREQUAL "Debug"
        OR (DEFINED ENV{DD_COMPILE_ABSEIL} AND ("$ENV{DD_COMPILE_ABSEIL}" STREQUAL "0" OR "$ENV{DD_COMPILE_ABSEIL}"
                                                                                          STREQUAL "false"))))
    target_link_libraries(test_memalloc_heap_map PRIVATE absl::flat_hash_map)
endif()

# Add NDEBUG flag for release builds
if(CMAKE_BUILD_TYPE STREQUAL "Release"
   OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo"
   OR CMAKE_BUILD_TYPE STREQUAL "MinSizeRel")
    target_compile_definitions(test_memalloc_heap_map PRIVATE NDEBUG)
else()
    target_compile_definitions(test_memalloc_heap_map PRIVATE UNDEBUG)
endif()

# Register the test with CTest
include(GoogleTest)
gtest_discover_tests(test_memalloc_heap_map)
