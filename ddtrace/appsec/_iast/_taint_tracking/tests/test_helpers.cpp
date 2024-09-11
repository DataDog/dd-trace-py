#include <Python.h>
#include <gtest/gtest.h>
#include <iostream>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include <Aspects/Helpers.h>
#include <Initializer/Initializer.h>
#include <TaintTracking/Source.h>

namespace py = pybind11;

class PyEnvCheck : public ::testing::Test
{
  protected:
    void SetUp() override { py::initialize_interpreter(); }

    void TearDown() override { py::finalize_interpreter(); }
};

class PyEnvWithContext : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        initializer = make_unique<Initializer>();
        py::initialize_interpreter();
        initializer->create_context();
    }

    void TearDown() override
    {
        initializer->reset_context();
        py::finalize_interpreter();
    }
};

using HasPyErrCheck = PyEnvCheck;

TEST_F(HasPyErrCheck, NoErrorReturnsFalse)
{
    EXPECT_FALSE(has_pyerr());
    EXPECT_STREQ(has_pyerr_as_string().c_str(), "");
}

TEST_F(HasPyErrCheck, ErrorReturnsTrue)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    EXPECT_TRUE(has_pyerr());
    EXPECT_STREQ(has_pyerr_as_string().c_str(), "Test error");
    PyErr_Clear();
}

TEST_F(HasPyErrCheck, ClearError)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    EXPECT_TRUE(has_pyerr());
    EXPECT_STREQ(has_pyerr_as_string().c_str(), "Test error");

    // Clear the error
    PyErr_Clear();
    EXPECT_FALSE(has_pyerr());
    EXPECT_STREQ(has_pyerr_as_string().c_str(), "");
}

using GetTagCheck = ::testing::Test;

TEST_F(GetTagCheck, HandlesEmptyString)
{
    std::string input = "";
    std::string expected_output = EVIDENCE_MARKS::BLANK;
    EXPECT_STREQ(get_tag(input).c_str(), expected_output.c_str());
}

TEST_F(GetTagCheck, HandlesNonEmptyString)
{
    std::string input = "example";
    std::string expected_output = std::string(EVIDENCE_MARKS::LESS) + "example" + std::string(EVIDENCE_MARKS::GREATER);
    EXPECT_STREQ(get_tag(input).c_str(), expected_output.c_str());
}

TEST_F(GetTagCheck, HandlesSpecialCharacters)
{
    std::string input = "special!@#";
    std::string expected_output =
      std::string(EVIDENCE_MARKS::LESS) + "special!@#" + std::string(EVIDENCE_MARKS::GREATER);
    EXPECT_STREQ(get_tag(input).c_str(), expected_output.c_str());
}
using GetDefaultContentCheck = ::testing::Test;

TEST_F(GetDefaultContentCheck, HandlesEmptySourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "";
    std::string expected_output = "";
    EXPECT_STREQ(get_default_content(taint_range).c_str(), expected_output.c_str());
}

TEST_F(GetDefaultContentCheck, HandlesNonEmptySourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "example";
    std::string expected_output = "example";
    EXPECT_STREQ(get_default_content(taint_range).c_str(), expected_output.c_str());
}

TEST_F(GetDefaultContentCheck, HandlesSpecialCharactersInSourceName)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->source.name = "special!@#";
    std::string expected_output = "special!@#";
    EXPECT_STREQ(get_default_content(taint_range).c_str(), expected_output.c_str());
}

using MapperReplaceCheck = PyEnvCheck;

TEST_F(MapperReplaceCheck, HandlesNullTaintRange)
{
    optional<const py::dict> new_ranges = py::dict();
    EXPECT_STREQ(mapper_replace(nullptr, new_ranges).c_str(), "");
}

TEST_F(MapperReplaceCheck, HandlesNullNewRanges)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    EXPECT_STREQ(mapper_replace(taint_range, nullopt).c_str(), "");
}

TEST_F(MapperReplaceCheck, HandlesNonExistingRange)
{
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    optional<const py::dict> new_ranges = py::dict();
    EXPECT_STREQ(mapper_replace(taint_range, new_ranges).c_str(), "");
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

    EXPECT_STREQ(mapper_replace(taint_range, new_ranges).c_str(), std::to_string(new_range->get_hash()).c_str());
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

using AsFormattedEvidenceCheck = PyEnvWithContext;
using AsFormattedEvidenceCheckNoContext = PyEnvCheck;

TEST_F(AsFormattedEvidenceCheckNoContext, NoTaintMapSameString)
{
    const py::str text("This is a test string.");
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 4, source) };
    const py::str result = as_formatted_evidence(text, taint_ranges);
    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(text).c_str());
}

TEST_F(AsFormattedEvidenceCheck, NoTaintRanges)
{
    std::string text = "This is a test string.";
    TaintRangeRefs taint_ranges; // Empty ranges
    std::string result = as_formatted_evidence(text, taint_ranges, std::nullopt);
    EXPECT_STREQ(result.c_str(), text.c_str());
}

TEST_F(AsFormattedEvidenceCheck, SingleTaintRangeWithNoMapper)
{
    const std::string text = "This is a test string.";
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 4, source) };
    const std::string expected_result = "This :+-<source1>is a<source1>-+: test string."; // Expected tagged output
    const std::string result = as_formatted_evidence(text, taint_ranges, nullopt, nullopt);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

TEST_F(AsFormattedEvidenceCheck, MultipleTaintRangesWithNoMapper)
{
    const std::string text = "This is a test string.";
    Source source1("source1", "sample_value1", OriginType::BODY);
    Source source2("source2", "sample_value2", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source1),
                                    std::make_shared<TaintRange>(10, 4, source2) };
    const std::string expected_result = "This :+-<source1>is<source1>-+: a :+-<source2>test<source2>-+: string.";
    const std::string result = as_formatted_evidence(text, taint_ranges, nullopt, nullopt);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

TEST_F(AsFormattedEvidenceCheck, DefaultTagMappingModeIsMapper)
{
    const std::string text = "This is a test string.";
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source) };

    const std::string expected_result = "This :+-<3485454368>is<3485454368>-+: a test string.";
    const std::string result = as_formatted_evidence(text, taint_ranges);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

TEST_F(AsFormattedEvidenceCheck, MultipleRangesWithMapper)
{
    const std::string text = "This is a test string.";
    Source source1("source1", "sample_value", OriginType::BODY);
    Source source2("source2", "sample_value", OriginType::PARAMETER);
    TaintRangeRefs taint_ranges = {
        std::make_shared<TaintRange>(5, 2, source1),
        std::make_shared<TaintRange>(10, 4, source2),
    };

    const std::string expected_result =
      "This :+-<3485454368>is<3485454368>-+: a :+-<891889858>test<891889858>-+: string.";
    const std::string result = as_formatted_evidence(text, taint_ranges);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

// FIXME: same problem as mapper_replace test above
TEST_F(AsFormattedEvidenceCheck, DISABLED_SingleTaintRangeWithMapperReplace)
{
    const std::string text = "This is a test string.";
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source) };

    py::dict new_ranges;
    TaintRange new_range(5, 2, Source("new_source", "sample_value", OriginType::BODY));
    new_ranges[py::cast(taint_ranges[0])] = new_range;

    const std::string expected_result = "This :+-<new_source>is<new_source>-+: a test string.";
    const std::string result = as_formatted_evidence(text, taint_ranges, TagMappingMode::Mapper_Replace, new_ranges);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

TEST_F(AsFormattedEvidenceCheck, EmptyTextWithTaintRanges)
{
    const std::string text;
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(0, 1, source) };
    const std::string expected_result;
    const std::string result = as_formatted_evidence(text, taint_ranges, nullopt, nullopt);
    EXPECT_STREQ(result.c_str(), expected_result.c_str());
}

using AllAsFormattedEvidenceCheck = PyEnvWithContext;
using AllAsFormattedEvidenceCheckNoContext = PyEnvCheck;

TEST_F(AllAsFormattedEvidenceCheckNoContext, NoTaintMapSameString)
{
    const py::str text("This is a test string.");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Mapper);
    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(text).c_str());
}

TEST_F(AllAsFormattedEvidenceCheck, NoRangesSameString)
{
    const py::str text("This is a test string.");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Mapper);
    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(text).c_str());
}

TEST_F(AllAsFormattedEvidenceCheck, SingleTaintRangeWithNormalMapper)
{
    py::str text("This is a test string.");
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source) };
    api_set_ranges(text, taint_ranges);

    const py::str expected_result("This :+-<source1>is<source1>-+: a test string.");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Normal);

    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(expected_result).c_str());
}

TEST_F(AllAsFormattedEvidenceCheck, SingleTaintRangeWithMapper)
{
    py::str text("This is a test string.");
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source) };
    api_set_ranges(text, taint_ranges);

    const py::str expected_result("This :+-<3485454368>is<3485454368>-+: a test string.");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Mapper);

    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(expected_result).c_str());
}

// See above
TEST_F(AllAsFormattedEvidenceCheck, DISABLED_SingleTaintRangeWithMapperReplace)
{
    py::str text("This is a test string.");
    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs taint_ranges = { std::make_shared<TaintRange>(5, 2, source) };
    api_set_ranges(text, taint_ranges);

    py::dict new_ranges;
    TaintRange new_range(5, 2, Source("new_source", "sample_value", OriginType::BODY));
    new_ranges[py::cast(taint_ranges[0])] = new_range;

    const py::str expected_result("This :+-<new_source>is<new_source>-+: a test string.");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Mapper_Replace);

    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(expected_result).c_str());
}

TEST_F(AllAsFormattedEvidenceCheck, EmptyText)
{
    const py::str text("");
    const py::str result = all_as_formatted_evidence(text, TagMappingMode::Mapper);
    EXPECT_STREQ(AnyTextObjectToString(result).c_str(), AnyTextObjectToString(text).c_str());
}