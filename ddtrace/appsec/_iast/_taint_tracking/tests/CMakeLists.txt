include(FetchContent)
FetchContent_Declare(
  googletest
  URL https://github.com/google/googletest/archive/refs/tags/release-1.11.0.zip
)
FetchContent_MakeAvailable(googletest)

enable_testing()

include_directories(${PYTHON_INCLUDE_DIRS})
include_directories(${PYBIND11_INCLUDE_DIRS})

file(GLOB TEST_SOURCES "*.cpp")
add_executable(native_tests ${TEST_SOURCES})

# Link test executable against gtest & gtest_main
target_link_libraries(native_tests gtest gtest_main ${PYTHON_LIBRARIES} pybind11::module)

# Discover tests
include(GoogleTest)
gtest_discover_tests(native_tests)
