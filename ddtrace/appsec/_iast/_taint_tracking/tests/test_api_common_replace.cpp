#include <gtest/gtest.h>
#include <pybind11/embed.h>

#include <aspects/helpers.h>
#include <tests/test_common.hpp>

namespace py = pybind11;

using ApiCommonReplaceCheck = PyEnvWithContext;

TEST_F(ApiCommonReplaceCheck, UnicodeUpperPreservesRanges)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::str s("helloWorld");
    Source src("S", "v", OriginType::BODY);
    // taint "World" -> start=5, len=5
    TaintRangeRefs ranges = { std::make_shared<TaintRange>(5, 5, src) };
    api_set_ranges(s, ranges, *ctx);

    // Act: call api_common_replace("upper", s)
    py::str method("upper");
    py::args a; // no positional args
    py::kwargs kw;
    auto res = api_common_replace<py::str>(method, s, a, kw);

    // Assert: value was uppercased and ranges preserved
    ASSERT_FALSE(res.is_none());
    EXPECT_EQ(res.cast<std::string>(), std::string("HELLOWORLD"));

    EXPECT_RANGESEQ(py::handle(res), py::handle(s));
}

TEST_F(ApiCommonReplaceCheck, BytesUpperPreservesRanges)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::bytes b("abcXYZ");
    Source src("B", "v", OriginType::PARAMETER);
    // taint "XYZ" -> start=3, len=3
    TaintRangeRefs ranges = { std::make_shared<TaintRange>(3, 3, src) };
    api_set_ranges(b, ranges, *ctx);

    // Act
    py::str method("upper");
    py::args a;
    py::kwargs kw;
    auto res = api_common_replace<py::bytes>(method, b, a, kw);

    // Assert
    ASSERT_FALSE(res.is_none());
    EXPECT_EQ(res.cast<std::string>(), std::string("ABCXYZ"));

    EXPECT_RANGESEQ(py::handle(res), py::handle(b));
}

TEST_F(ApiCommonReplaceCheck, NoRangesReturnsUnTainted)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::str s("abc");

    // Act
    py::str method("upper");
    py::args a;
    py::kwargs kw;
    auto res = api_common_replace<py::str>(method, s, a, kw);

    // Assert
    ASSERT_FALSE(res.is_none());
    EXPECT_EQ(res.cast<std::string>(), std::string("ABC"));

    auto res_ranges = api_get_ranges(py::handle(res));
    EXPECT_TRUE(res_ranges.empty());
}
