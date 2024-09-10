#include <Python.h>
#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include <Aspects/Helpers.h>

namespace py = pybind11;

class PyEnvCheck : public ::testing::Test
{
  protected:
    void SetUp() override { py::initialize_interpreter(); }

    void TearDown() override { py::finalize_interpreter(); }
};

using HasPyErrCheck = PyEnvCheck;

TEST_F(HasPyErrCheck, NoErrorReturnsFalse)
{
    EXPECT_FALSE(has_pyerr());
    EXPECT_EQ(has_pyerr_as_string(), "");
}

TEST_F(HasPyErrCheck, ErrorReturnsTrue)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    EXPECT_TRUE(has_pyerr());
    EXPECT_EQ(has_pyerr_as_string(), "Test error");
    PyErr_Clear();
}

TEST_F(HasPyErrCheck, ClearError)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    EXPECT_TRUE(has_pyerr());
    EXPECT_EQ(has_pyerr_as_string(), "Test error");

    // Clear the error
    PyErr_Clear();
    EXPECT_FALSE(has_pyerr());
    EXPECT_EQ(has_pyerr_as_string(), "");
}

using GetTagCheck = ::testing::Test;

TEST_F(GetTagCheck, HandlesEmptyString)
{
    std::string input = "";
    std::string expected_output = EVIDENCE_MARKS::BLANK;
    EXPECT_EQ(get_tag(input), expected_output);
}

TEST_F(GetTagCheck, HandlesNonEmptyString)
{
    std::string input = "example";
    std::string expected_output = std::string(EVIDENCE_MARKS::LESS) + "example" + std::string(EVIDENCE_MARKS::GREATER);
    EXPECT_EQ(get_tag(input), expected_output);
}

TEST_F(GetTagCheck, HandlesSpecialCharacters)
{
    std::string input = "special!@#";
    std::string expected_output =
      std::string(EVIDENCE_MARKS::LESS) + "special!@#" + std::string(EVIDENCE_MARKS::GREATER);
    EXPECT_EQ(get_tag(input), expected_output);
}
using GetDefaultContentCheck = ::testing::Test;

TEST_F(GetDefaultContentCheck, HandlesEmptySourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "";
    std::string expected_output = "";
    EXPECT_EQ(get_default_content(taint_range), expected_output);
}

TEST_F(GetDefaultContentCheck, HandlesNonEmptySourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "example";
    std::string expected_output = "example";
    EXPECT_EQ(get_default_content(taint_range), expected_output);
}

TEST_F(GetDefaultContentCheck, HandlesSpecialCharactersInSourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "special!@#";
    std::string expected_output = "special!@#";
    EXPECT_EQ(get_default_content(taint_range), expected_output);
}

using MapperReplaceCheck = PyEnvCheck;

TEST_F(MapperReplaceCheck, HandlesNullTaintRange)
{
    optional<const py::dict> new_ranges = py::dict();
    EXPECT_EQ(mapper_replace(nullptr, new_ranges), "");
}

TEST_F(MapperReplaceCheck, HandlesNullNewRanges)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    EXPECT_EQ(mapper_replace(taint_range, nullopt), "");
}

TEST_F(MapperReplaceCheck, HandlesNonExistingRange)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    optional<const py::dict> new_ranges = py::dict();
    EXPECT_EQ(mapper_replace(taint_range, new_ranges), "");
}

// FIXME: not working, check with Alberto
TEST_F(MapperReplaceCheck, DISABLED_HandlesExistingRange)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 5;
    taint_range->source.name = "example";

    TaintRangePtr new_range = std::make_shared<TaintRange>();
    new_range->start = 0;
    new_range->length = 5;
    new_range->source.name = "new_example";

    py::dict new_ranges;
    new_ranges[py::cast(taint_range)] = py::cast(new_range);

    EXPECT_EQ(mapper_replace(taint_range, new_ranges), std::to_string(new_range->get_hash()));
}

using GetNumTest = PyEnvCheck;

TEST_F(GetNumTest, ValidNumber)
{
    std::string valid_str = "12345";
    unsigned long int result = getNum(valid_str);
    EXPECT_EQ(result, 12345);
}

TEST_F(GetNumTest, EmptyString)
{
    std::string empty_str = "";
    unsigned long int result = getNum(empty_str);
    EXPECT_EQ(result, static_cast<unsigned long int>(-1));
}

TEST_F(GetNumTest, InvalidString)
{
    std::string invalid_str = "abc";
    unsigned long int result = getNum(invalid_str);
    EXPECT_EQ(result, static_cast<unsigned long int>(-1));
}

TEST_F(GetNumTest, OutOfRangeNumber)
{
    std::string out_of_range_str = "999999999999999999999999";
    unsigned long int result = getNum(out_of_range_str);
    EXPECT_EQ(result, static_cast<unsigned long int>(-1)); // Should return -1 due to exception
}

TEST_F(GetNumTest, MaxUnsignedLong)
{
    std::string max_ulong_str = std::to_string(ULONG_MAX);
    unsigned long int result = getNum(max_ulong_str);
    EXPECT_EQ(result, ULONG_MAX);
}