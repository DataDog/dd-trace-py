include(GoogleTest)

file(GLOB_RECURSE NATIVE_TEST_SOURCE *.cpp)
add_executable(native_test ${NATIVE_TEST_SOURCE})

set_target_properties(native_test PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED YES
    CXX_EXTENSIONS NO)

target_include_directories(native_test PRIVATE ${NATIVE_PUBLIC_INCLUDES} ${NATIVE_PRIVATE_INCLUDES})

# Link GoogleTest
target_link_libraries(native_test gtest gtest_main)

# Use gtest_discover_tests to automatically discover and run tests
gtest_discover_tests(native_test)

