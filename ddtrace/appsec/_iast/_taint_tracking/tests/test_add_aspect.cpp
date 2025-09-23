#include <gtest/gtest.h>
#include <pybind11/embed.h>

#include <aspects/aspect_operator_add.h>
#include <aspects/helpers.h>
#include <tests/test_common.hpp>

namespace py = pybind11;

using AddAspectCheck = PyEnvWithContext;

TEST_F(AddAspectCheck, UntaintedOperandsProduceNoRanges)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::str left("hello");
    py::str right(" world");

    PyObject* args_array[2];
    args_array[0] = left.ptr();
    args_array[1] = right.ptr();

    // Act
    PyObject* res = api_add_aspect(nullptr, args_array, 2);

    // Assert
    ASSERT_NE(res, nullptr);
    auto res_ranges = api_get_ranges(py::handle(res));
    EXPECT_TRUE(res_ranges.empty());

    // Cleanup
    Py_DecRef(res);
}

TEST_F(AddAspectCheck, LeftTaintedRightUntaintedKeepsLeftRanges)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::str left("hello");   // len 5
    py::str right(" world"); // len 6

    // Taint entire left string
    Source src("left", "v", OriginType::BODY);
    TaintRangeRefs left_ranges = { std::make_shared<TaintRange>(0, 5, src) };

    api_set_ranges(left, left_ranges, *ctx);

    PyObject* args_array[2];
    args_array[0] = left.ptr();
    args_array[1] = right.ptr();

    // Act
    PyObject* res = api_add_aspect(nullptr, args_array, 2);

    // Assert
    ASSERT_NE(res, nullptr);
    // Content should be concatenation
    const std::string res_str = py::reinterpret_borrow<py::str>(res).cast<std::string>();
    EXPECT_EQ(res_str, std::string("hello world"));

    // Ranges should equal left ranges (no shift)
    EXPECT_RANGESEQ(py::handle(res), left);

    // Cleanup
    Py_DecRef(res);
}

TEST_F(AddAspectCheck, BothTaintedRightRangesAreShifted)
{
    // Start context
    auto ctx = taint_engine_context->start_request_context();
    ASSERT_TRUE(ctx.has_value());
    this->context_id = ctx;

    // Arrange
    py::str left("abcde"); // len 5
    py::str right("XYZ");  // len 3

    Source sleft("L", "v", OriginType::BODY);
    Source sright("R", "v", OriginType::PARAMETER);

    // Left tainted in the middle [1,2] => "bc"
    TaintRangeRefs left_ranges = { std::make_shared<TaintRange>(1, 2, sleft) };
    api_set_ranges(left, left_ranges, *ctx);

    // Right tainted at start [0,1] => "X"
    TaintRangeRefs right_ranges = { std::make_shared<TaintRange>(0, 1, sright) };
    api_set_ranges(right, right_ranges, *ctx);

    PyObject* args_array[2];
    args_array[0] = left.ptr();
    args_array[1] = right.ptr();

    // Act
    PyObject* res = api_add_aspect(nullptr, args_array, 2);

    // Assert
    ASSERT_NE(res, nullptr);
    const std::string res_str = py::reinterpret_borrow<py::str>(res).cast<std::string>();
    EXPECT_EQ(res_str, std::string("abcdeXYZ"));

    // Expected ranges: left [1,2] and right shifted by len(left)=5 => [5,1]
    TaintRangeRefs expected;
    expected.push_back(std::make_shared<TaintRange>(1, 2, sleft));
    expected.push_back(std::make_shared<TaintRange>(5, 1, sright));

    auto actual = api_get_ranges(py::handle(res));
    EXPECT_RANGESEQ(actual, expected);

    // Cleanup
    Py_DecRef(res);
}
