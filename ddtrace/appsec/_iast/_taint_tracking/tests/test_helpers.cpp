#include <aspects/helpers.h>
#include <tests/test_common.hpp>
#include <utils/python_error_guard.h>

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

using PythonErrorGuardCheck = PyEnvCheck;

TEST_F(PythonErrorGuardCheck, NoError)
{
    PythonErrorGuard guard;
    EXPECT_FALSE(guard.has_error());
    EXPECT_STREQ(guard.error_as_stdstring().c_str(), "");
}

TEST_F(PythonErrorGuardCheck, Error)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    {
        PythonErrorGuard guard;
        EXPECT_TRUE(guard.has_error());
        EXPECT_STREQ(guard.error_as_stdstring().c_str(), "Test error");
        EXPECT_STREQ(guard.error_as_pystr().cast<std::string>().c_str(), "Test error");
    }
    PyErr_Clear();
}

TEST_F(PythonErrorGuardCheck, ErrorIsClearedThenNoErrorInGuard)
{
    PyErr_SetString(PyExc_RuntimeError, "Test error");
    {
        PythonErrorGuard guard;
        EXPECT_TRUE(guard.has_error());
        EXPECT_STREQ(guard.error_as_stdstring().c_str(), "Test error");
        EXPECT_STREQ(guard.error_as_pystr().cast<std::string>().c_str(), "Test error");
    }
    PyErr_Clear();
    PythonErrorGuard guard;
    EXPECT_FALSE(guard.has_error());
}

void
set_python_exception_with_traceback()
{
    py::gil_scoped_acquire acquire;

    PyObject* exc_type = PyExc_ZeroDivisionError;
    PyObject* exc_value = PyUnicode_FromString("division by zero");

    // Define Python code that raises an exception
    const char* code = R"(
def faulty_function():
    return 1 / 0  # This will raise ZeroDivisionError

try:
    faulty_function()
except ZeroDivisionError:
    import sys
    import traceback
    exc_type, exc_value, exc_tb = sys.exc_info()
)";

    // Execute the code
    int result = PyRun_SimpleString(code);
    if (result != 0) {
        // If execution failed, exit the function
        return;
    }

    // Retrieve the traceback object from the main module
    PyObject* main_module = PyImport_AddModule("__main__");       // Borrowed reference
    PyObject* main_dict = PyModule_GetDict(main_module);          // Borrowed reference
    PyObject* exc_tb = PyDict_GetItemString(main_dict, "exc_tb"); // Borrowed reference

    // Increment references as PyErr_Restore steals references
    Py_XINCREF(exc_type);
    Py_XINCREF(exc_value);
    Py_XINCREF(exc_tb);

    // Restore the exception with traceback
    PyErr_Restore(exc_type, exc_value, exc_tb);
}

TEST_F(PythonErrorGuardCheck, ErrorWithTraceback)
{
    {
        // Set the Python exception with traceback manually
        set_python_exception_with_traceback();

        // Instantiate PythonErrorGuard to capture the current Python error
        PythonErrorGuard guard;

        // Verify that an error was captured
        EXPECT_TRUE(guard.has_error()) << "PythonErrorGuard did not capture the error.";

        // Retrieve the traceback as std::string
        const auto tb_str = guard.traceback_as_stdstring();

        EXPECT_FALSE(tb_str.empty()) << "Traceback is empty.";
        EXPECT_NE(tb_str.find("faulty_function"), std::string::npos) << "Traceback does not contain 'faulty_function'.";
        EXPECT_NE(tb_str.find("ZeroDivisionError"), std::string::npos)
          << "Traceback does not contain 'ZeroDivisionError'.";

        // Retrieve the traceback as py::str
        const py::str tb_pystr = guard.traceback_as_pystr();
        EXPECT_FALSE(tb_pystr.is_none()) << "Traceback py::str is empty.";

        const auto tb_pystr_str = tb_pystr.cast<std::string>();
        EXPECT_FALSE(tb_pystr_str.empty()) << "Traceback py::str is empty after cast.";
        EXPECT_NE(tb_pystr_str.find("faulty_function"), std::string::npos)
          << "Traceback py::str does not contain 'faulty_function'.";
        EXPECT_NE(tb_pystr_str.find("ZeroDivisionError"), std::string::npos)
          << "Traceback py::str does not contain 'ZeroDivisionError'.";
    }
    PyErr_Clear();
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

    auto taint_range_1_hash = taint_ranges[0]->get_hash();
    const std::string expected_result = "This :+-<" + std::to_string(taint_range_1_hash) + ">is<" +
                                        std::to_string(taint_range_1_hash) + ">-+: a test string.";
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

    auto taint_range_1_hash = taint_ranges[0]->get_hash();
    auto taint_range_2_hash = taint_ranges[1]->get_hash();
    const std::string expected_result =
      "This :+-<" + std::to_string(taint_range_1_hash) + ">is<" + std::to_string(taint_range_1_hash) + ">-+: a :+-<" +
      std::to_string(taint_range_2_hash) + ">test<" + std::to_string(taint_range_2_hash) + ">-+: string.";
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

    auto taint_range_1_hash = taint_ranges[0]->get_hash();
    const py::str expected_result("This :+-<" + std::to_string(taint_range_1_hash) + ">is<" +
                                  std::to_string(taint_range_1_hash) + ">-+: a test string.");
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

using ParseParamsCheck = PyEnvCheck;

TEST_F(ParseParamsCheck, PositionalArgumentPresent)
{
    py::args args = py::make_tuple(42);
    py::kwargs kwargs;
    py::object default_value = py::int_(0);

    py::object result = parse_param(0, "key", default_value, args, kwargs);
    EXPECT_EQ(result.cast<int>(), 42);
}

TEST_F(ParseParamsCheck, KeywordArgumentPresent)
{
    py::args args;
    py::kwargs kwargs;
    kwargs["key"] = py::int_(42);
    py::object default_value = py::int_(0);

    py::object result = parse_param(0, "key", default_value, args, kwargs);
    EXPECT_EQ(result.cast<int>(), 42);
}

TEST_F(ParseParamsCheck, NoArgumentUsesDefault)
{
    py::args args;
    py::kwargs kwargs;
    py::object default_value = py::int_(42);

    py::object result = parse_param(0, "key", default_value, args, kwargs);
    EXPECT_EQ(result.cast<int>(), 42);
}

TEST_F(ParseParamsCheck, PositionalOverridesKeyword)
{
    py::args args = py::make_tuple(100);
    py::kwargs kwargs;
    kwargs["key"] = py::int_(42);
    py::object default_value = py::int_(0);

    py::object result = parse_param(0, "key", default_value, args, kwargs);
    EXPECT_EQ(result.cast<int>(), 100);
}

TEST_F(ParseParamsCheck, HandlesMissingKeyword)
{
    py::args args;
    py::kwargs kwargs;
    py::object default_value = py::str("default_value");

    py::object result = parse_param(0, "missing_key", default_value, args, kwargs);
    EXPECT_STREQ(result.cast<std::string>().c_str(), "default_value");
}

TEST(SplitTaints, EmptyString)
{
    std::string input = "";
    std::vector<std::string> expected_output = { "" };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

TEST(SplitTaints, NoTaintsInString)
{
    std::string input = "This is a regular string.";
    std::vector<std::string> expected_output = { "This is a regular string." };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

TEST(SplitTaints, SingleTaintInString)
{
    std::string input = "This is a :+-<source1>test<source1>-+: string.";
    std::vector<std::string> expected_output = { "This is a ", ":+-<source1>", "test", "<source1>-+:", " string." };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

TEST(SplitTaints, MultipleTaintsInString)
{
    std::string input = "This :+-<source1>is<source1>-+: a :+-<source2>test<source2>-+: string.";
    std::vector<std::string> expected_output = { "This ",        ":+-<source1>", "is",           "<source1>-+:", " a ",
                                                 ":+-<source2>", "test",         "<source2>-+:", " string." };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

TEST(SplitTaints, TaintsAtStartAndEnd)
{
    std::string input = ":+-<source1>Start<source1>-+: and :+-<source2>End<source2>-+:";
    std::vector<std::string> expected_output = { "",      ":+-<source1>", "Start", "<source1>-+:",
                                                 " and ", ":+-<source2>", "End",   "<source2>-+:" };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

TEST(SplitTaints, ConsecutiveTaints)
{
    std::string input = "Text :+-<source1>taint1<source1>-+: :+-<source2>taint2<source2>-+: after.";
    std::vector<std::string> expected_output = { "Text ",        ":+-<source1>", "taint1",       "<source1>-+:", " ",
                                                 ":+-<source2>", "taint2",       "<source2>-+:", " after." };
    std::vector<std::string> result = split_taints(input);
    EXPECT_EQ(result, expected_output);
}

using SetRangesOnSplittedCheck = PyEnvWithContext;

TEST_F(SetRangesOnSplittedCheck, EmptySourceAndSplit)
{
    py::str source_str = "";
    py::list split_result;
    TaintRangeRefs source_ranges;
    auto tx_map = Initializer::get_tainting_map();
    bool result = set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, false);
    EXPECT_FALSE(result);

    for (const auto& item : split_result) {
        EXPECT_STREQ(AnyTextObjectToString(item.cast<py::str>()).c_str(), "");
        auto ranges = get_ranges(item.ptr(), tx_map);
        EXPECT_TRUE(ranges.first.empty());
    }
}

TEST_F(SetRangesOnSplittedCheck, SingleSplitWithoutSeparator)
{
    py::str source_str = "This is a test string.";
    py::list split_result;
    split_result.append(py::str("This"));
    split_result.append(py::str("is a test string."));

    Source source("source1", "sample_value", OriginType::BODY);
    TaintRangeRefs source_ranges = { std::make_shared<TaintRange>(0, 4, source) };
    api_set_ranges(source_str, source_ranges);
    auto tx_map = Initializer::get_tainting_map();
    bool result = set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, false);
    EXPECT_TRUE(result);

    auto first = split_result[0];
    auto first_ranges = get_ranges(first.ptr(), tx_map);
    EXPECT_EQ(first_ranges.first.size(), 1);
    EXPECT_EQ(first_ranges.first[0]->start, 0);
    EXPECT_EQ(first_ranges.first[0]->length, 4);

    auto last = split_result[1];
    auto last_ranges = get_ranges(last.ptr(), tx_map);
    EXPECT_TRUE(last_ranges.first.empty());
}

TEST_F(SetRangesOnSplittedCheck, MultipleSplitsNoSeparator)
{
    py::str source_str = "This is a test string.";
    py::list split_result;
    split_result.append(py::str("This"));
    split_result.append(py::str("is"));
    split_result.append(py::str("a"));
    split_result.append(py::str("test"));
    split_result.append(py::str("string."));

    Source source1("source1", "sample_value1", OriginType::BODY);
    Source source2("source2", "sample_value2", OriginType::BODY);
    TaintRangeRefs source_ranges = {
        std::make_shared<TaintRange>(0, 4, source1), // Taint "This"
        std::make_shared<TaintRange>(10, 4, source2) // Taint "test"
    };
    api_set_ranges(source_str, source_ranges);
    auto tx_map = Initializer::get_tainting_map();

    bool result = set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, false);
    EXPECT_TRUE(result);

    // Check first split part "This"
    auto first = split_result[0];
    auto first_ranges = get_ranges(first.ptr(), tx_map);
    EXPECT_EQ(first_ranges.first.size(), 1);
    EXPECT_EQ(first_ranges.first[0]->start, 0);
    EXPECT_EQ(first_ranges.first[0]->length, 4);

    // Check middle split part "test"
    auto test_part = split_result[3];
    auto test_ranges = get_ranges(test_part.ptr(), tx_map);
    EXPECT_EQ(test_ranges.first.size(), 1);
    EXPECT_EQ(test_ranges.first[0]->start, 0); // Position within "test"
    EXPECT_EQ(test_ranges.first[0]->length, 4);

    // Check that other parts have no ranges
    for (int i : { 1, 2, 4 }) {
        auto part = split_result[i];
        auto part_ranges = get_ranges(part.ptr(), tx_map);
        EXPECT_TRUE(part_ranges.first.empty());
    }
}

TEST_F(SetRangesOnSplittedCheck, SplitWithSeparatorIncluded)
{
    py::str source_str = "This|is|a|test|string.";
    py::list split_result;
    split_result.append(py::str("This"));
    split_result.append(py::str("|is"));
    split_result.append(py::str("|a"));
    split_result.append(py::str("|test"));
    split_result.append(py::str("|string."));

    Source source1("source1", "sample_value1", OriginType::BODY);
    Source source2("source2", "sample_value2", OriginType::BODY);
    TaintRangeRefs source_ranges = {
        std::make_shared<TaintRange>(0, 4, source1), // Taint "This"
        std::make_shared<TaintRange>(10, 4, source2) // Taint "test"
    };
    api_set_ranges(source_str, source_ranges);
    auto tx_map = Initializer::get_tainting_map();

    bool result = set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, true);
    EXPECT_TRUE(result);

    // Check first split part "This"
    auto first = split_result[0];
    auto first_ranges = get_ranges(first.ptr(), tx_map);
    EXPECT_EQ(first_ranges.first.size(), 1);
    EXPECT_EQ(first_ranges.first[0]->start, 0);
    EXPECT_EQ(first_ranges.first[0]->length, 4);

    // Check middle split part "test"
    auto test_part = split_result[3];
    auto test_ranges = get_ranges(test_part.ptr(), tx_map);
    EXPECT_EQ(test_ranges.first.size(), 1);
    EXPECT_EQ(test_ranges.first[0]->start, 1); // Position within "test"
    EXPECT_EQ(test_ranges.first[0]->length, 4);

    // Check that other parts have no ranges
    for (int i : { 1, 2, 4 }) {
        auto part = split_result[i];
        auto part_ranges = get_ranges(part.ptr(), tx_map);
        EXPECT_TRUE(part_ranges.first.empty());
    }
}

TEST_F(SetRangesOnSplittedCheck, EmptyRanges)
{
    py::str source_str = "This is a test string.";
    py::list split_result;
    split_result.append(py::str("This"));
    split_result.append(py::str("is a test string."));

    TaintRangeRefs source_ranges; // Empty ranges
    auto tx_map = Initializer::get_tainting_map();

    bool result = set_ranges_on_splitted(source_str, source_ranges, split_result, tx_map, false);
    EXPECT_FALSE(result);

    // Check that no ranges are applied to the split result
    for (const auto& item : split_result) {
        auto item_ranges = get_ranges(item.ptr(), tx_map);
        EXPECT_TRUE(item_ranges.first.empty());
    }
}

using ProcessFlagAddedArgsTest = PyEnvCheck;

TEST_F(ProcessFlagAddedArgsTest, NoAddedArgsOriginalNone)
{
    PyObject* orig_function = Py_None;
    int flag_added_args = 0;
    py::tuple args = py::make_tuple("arg1", "arg2");
    py::dict kwargs;

    PyObject* result = process_flag_added_args(orig_function, flag_added_args, args.ptr(), kwargs.ptr());

    // Should return args as no slicing is required
    EXPECT_EQ(result, args.ptr());
}

// Test with added args, original function is None
TEST_F(ProcessFlagAddedArgsTest, AddedArgsOriginalNone)
{
    PyObject* orig_function = Py_None;
    int flag_added_args = 1;
    py::tuple args = py::make_tuple("arg1", "arg2", "added_arg");
    py::dict kwargs;

    PyObject* result = process_flag_added_args(orig_function, flag_added_args, args.ptr(), kwargs.ptr());

    // Should return the full argument list since no slicing is needed
    EXPECT_EQ(result, args.ptr());
}

// Test with added args, original function is custom
TEST_F(ProcessFlagAddedArgsTest, AddedArgsOriginalCustomFunction)
{
    PyObject* orig_function = Py_None;
    py::object custom_function = py::cpp_function([](py::str arg1, py::str arg2) { return arg1; });
    orig_function = custom_function.ptr();

    int flag_added_args = 1;
    py::tuple args = py::make_tuple("arg1", "arg2", "added_arg");
    py::dict kwargs;

    PyObject* result = process_flag_added_args(orig_function, flag_added_args, args.ptr(), kwargs.ptr());
    EXPECT_STREQ(AnyTextObjectToString(py::reinterpret_borrow<py::tuple>(result)).c_str(), "arg2");
}

// Test with no added args, original function is custom
TEST_F(ProcessFlagAddedArgsTest, NoAddedArgsOriginalCustomFunction)
{
    py::object custom_function = py::cpp_function([](py::str arg1, py::str arg2) { return arg1; });
    PyObject* orig_function = custom_function.ptr();

    int flag_added_args = 0;
    py::tuple args = py::make_tuple("arg1", "arg2");
    py::dict kwargs;

    PyObject* result = process_flag_added_args(orig_function, flag_added_args, args.ptr(), kwargs.ptr());
    EXPECT_STREQ(AnyTextObjectToString(py::reinterpret_borrow<py::str>(result)).c_str(), "arg1");
}
