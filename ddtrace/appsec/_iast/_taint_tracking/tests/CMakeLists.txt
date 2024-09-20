include(FetchContent)
FetchContent_Declare(
        googletest
        URL https://github.com/google/googletest/archive/refs/tags/release-1.11.0.zip
)
FetchContent_MakeAvailable(googletest)

enable_testing()

include_directories(${PYTHON_INCLUDE_DIRS})
include_directories(${PYBIND11_INCLUDE_DIRS})

file(GLOB TEST_SOURCES "*.cpp" "*.hpp")
add_executable(native_tests ${TEST_SOURCES} ${SOURCE_FILES} ${HEADER_FILES})

if (CMAKE_BUILD_TYPE STREQUAL "Release")
    target_link_libraries(native_tests gtest gtest_main ${PYTHON_LIBRARIES} pybind11::module absl::node_hash_map)
else ()
    target_link_libraries(native_tests gtest gtest_main ${PYTHON_LIBRARIES} pybind11::module)
endif ()


# Discover tests
include(GoogleTest)
gtest_discover_tests(native_tests)
