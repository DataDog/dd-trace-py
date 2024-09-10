#include <Python.h>
#include <gtest/gtest.h>
#include <iostream> // JJJ
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

TEST_F(MapperReplaceCheck, HandlesExistingRange)
{
    cerr << "JJJ 1\n";
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    cerr << "JJJ 2\n";
    taint_range->start = 0;
    taint_range->length = 5;
    taint_range->source.name = "example";
    cerr << "JJJ 3\n";

    TaintRangePtr new_range = std::make_shared<TaintRange>();
    cerr << "JJJ 4\n";
    new_range->start = 0;
    new_range->length = 5;
    new_range->source.name = "new_example";
    cerr << "JJJ 5\n";

    py::dict new_ranges;
    cerr << "JJJ 5.1\n";
    new_ranges[py::cast(taint_range)] = py::cast(new_range);
    cerr << "JJJ 7\n";

    EXPECT_EQ(mapper_replace(taint_range, new_ranges), std::to_string(new_range->get_hash()));
}