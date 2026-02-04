#include <taint_tracking/source.h>
#include <tests/test_common.hpp>

#include <algorithm>
#include <map>
#include <string>
#include <vector>

using OriginConversionTest = ::testing::Test;

/**
 * C++ unit tests for origin_to_str/str_to_origin functions.
 *
 * IMPORTANT: These tests verify the C++ implementation is correct, but CANNOT detect
 * the PyBind11 return policy bug that was fixed in this PR.
 *
 * THE BUG (in source.cpp pyexport_source):
 * -----------------------------------------
 * BUGGY: m.def("origin_to_str", &origin_to_str, "origin"_a, py::return_value_policy::reference);
 * FIXED: m.def("origin_to_str", &origin_to_str, "origin"_a);
 *
 * WHY IT'S A BUG:
 * ---------------
 * - origin_to_str() returns std::string BY VALUE (a temporary string)
 * - py::return_value_policy::reference tells PyBind11 to return a reference to this temporary
 * - The temporary is destroyed after the function returns
 * - Python receives a dangling reference to freed memory
 * - Accessing this from Python causes undefined behavior (crashes, corruption)
 *
 * WHY THESE C++ TESTS DON'T CATCH IT:
 * ------------------------------------
 * These tests call the C++ functions directly (origin_to_str, str_to_origin), NOT through
 * the Python bindings. The C++ functions themselves are correctly implemented. The bug is
 * only in how PyBind11 exposes them to Python.
 *
 * To test this bug, you need to call the functions FROM PYTHON and use tools like:
 * - Valgrind: Can detect use-after-free when calling from Python
 * - AddressSanitizer: Can detect the issue in Python extension modules
 * - Python regression tests: tests/appsec/iast/taint_tracking/test_native_taint_source.py
 *
 * REGRESSION TESTING:
 * -------------------
 * The real regression tests are in Python:
 *   tests/appsec/iast/taint_tracking/test_native_taint_source.py
 *
 * Those tests call origin_to_str through the Python bindings and verify:
 * - Strings remain valid after function return
 * - Tuple construction works (production usage pattern from _metrics.py)
 * - String operations don't crash
 * - Garbage collection doesn't cause issues
 *
 * These C++ tests serve to:
 * - Verify the C++ implementation is correct
 * - Catch any memory issues in the C++ code itself (with ASAN)
 * - Document the expected behavior of the functions
 */

TEST(OriginConversion, OriginToStrReturnsValidString)
{
    // Test all OriginType values
    struct TestCase
    {
        OriginType origin;
        std::string expected;
    };

    std::vector<TestCase> test_cases = {
        { OriginType::PARAMETER, "http.request.parameter" },
        { OriginType::PARAMETER_NAME, "http.request.parameter.name" },
        { OriginType::HEADER, "http.request.header" },
        { OriginType::HEADER_NAME, "http.request.header.name" },
        { OriginType::PATH, "http.request.path" },
        { OriginType::BODY, "http.request.body" },
        { OriginType::QUERY, "http.request.query" },
        { OriginType::PATH_PARAMETER, "http.request.path.parameter" },
        { OriginType::COOKIE, "http.request.cookie.value" },
        { OriginType::COOKIE_NAME, "http.request.cookie.name" },
        { OriginType::GRPC_BODY, "http.request.grpc_body" },
    };

    for (const auto& test_case : test_cases) {
        // Call origin_to_str and store result
        std::string result = origin_to_str(test_case.origin);

        // Verify correctness
        EXPECT_EQ(result, test_case.expected);
        EXPECT_GT(result.length(), 0);
        EXPECT_EQ(result.substr(0, 12), "http.request");

        // Perform string operations that access memory
        std::string upper = result;
        std::transform(upper.begin(), upper.end(), upper.begin(), ::toupper);
        EXPECT_GT(upper.length(), 0);

        // Test string methods
        auto pos = result.find(".");
        EXPECT_NE(pos, std::string::npos);
    }
}

TEST(OriginConversion, MultipleCallsWithStringStorage)
{
    std::vector<std::string> stored_strings;

    // Make many calls and store the results
    for (int i = 0; i < 1000; ++i) {
        stored_strings.push_back(origin_to_str(OriginType::PARAMETER));
        stored_strings.push_back(origin_to_str(OriginType::HEADER));
        stored_strings.push_back(origin_to_str(OriginType::COOKIE));
    }

    // Verify all strings are valid
    for (const auto& s : stored_strings) {
        EXPECT_GT(s.length(), 0);
        EXPECT_EQ(s.substr(0, 12), "http.request");
    }
}

TEST(OriginConversion, ImmediateStringOperations)
{
    // Simulate creating tuples/pairs with the result
    for (int i = 0; i < 100; ++i) {
        // Get string and immediately use it
        std::string tag = "source_type:" + origin_to_str(OriginType::PARAMETER);
        EXPECT_EQ(tag, "source_type:http.request.parameter");

        // Simulate dict/map insertion
        std::map<std::string, std::string> data;
        data["origin"] = origin_to_str(OriginType::BODY);
        data["type"] = origin_to_str(OriginType::QUERY);

        EXPECT_EQ(data["origin"], "http.request.body");
        EXPECT_EQ(data["type"], "http.request.query");
    }
}

TEST(OriginConversion, StrToOriginReturnsValidEnum)
{
    struct TestCase
    {
        std::string input;
        OriginType expected;
    };

    std::vector<TestCase> test_cases = {
        { "http.request.parameter", OriginType::PARAMETER },
        { "http.request.parameter.name", OriginType::PARAMETER_NAME },
        { "http.request.header", OriginType::HEADER },
        { "http.request.header.name", OriginType::HEADER_NAME },
        { "http.request.path", OriginType::PATH },
        { "http.request.body", OriginType::BODY },
        { "http.request.query", OriginType::QUERY },
        { "http.request.path.parameter", OriginType::PATH_PARAMETER },
        { "http.request.cookie.value", OriginType::COOKIE },
        { "http.request.cookie.name", OriginType::COOKIE_NAME },
        { "http.request.grpc_body", OriginType::GRPC_BODY },
    };

    for (const auto& test_case : test_cases) {
        OriginType result = str_to_origin(test_case.input);
        EXPECT_EQ(result, test_case.expected);
    }
}

TEST(OriginConversion, RoundtripConversion)
{
    std::vector<OriginType> origins = { OriginType::PARAMETER, OriginType::HEADER,      OriginType::COOKIE,
                                        OriginType::BODY,      OriginType::QUERY,       OriginType::PATH,
                                        OriginType::GRPC_BODY, OriginType::COOKIE_NAME, OriginType::HEADER_NAME };

    for (const auto& original_origin : origins) {
        // Convert to string
        std::string origin_str = origin_to_str(original_origin);
        EXPECT_GT(origin_str.length(), 0);

        // Convert back to origin
        OriginType converted_origin = str_to_origin(origin_str);

        // Verify roundtrip
        EXPECT_EQ(converted_origin, original_origin);
    }
}
