#include <Aspects/AspectStr.h>
#include <Aspects/Helpers.h>
#include <iostream>
#include <tests/test_common.hpp>

using CheckAspectStr = PyEnvWithContext;

TEST_F(CheckAspectStr, StrWithStr)
{
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::str("test"))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "test");
}

TEST_F(CheckAspectStr, StrWithInteger)
{
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::int_(42))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "42");
}

TEST_F(CheckAspectStr, StrWithFloat)
{
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::float_(42.42))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "42.42");
}

TEST_F(CheckAspectStr, StrWithBytesNoEncoding)
{
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::bytes("test"))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "b'test'");
}

TEST_F(CheckAspectStr, StrWithBytesAndEncoding)
{
    auto result =
      api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::bytes("test"), py::str("utf-8"))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "test");
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorStrictButNoError)
{
    auto result = api_str_aspect(
      py::none(), 0, py::args(py::make_tuple(py::bytes("test"), py::str("utf-8"), py::str("strict"))), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "test");
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorStrictAndErrorRaisesUnicodeDecodeError)
{
    try {
        auto result =
          api_str_aspect(py::none(),
                         0,
                         py::args(py::make_tuple(py::bytes("test\244"), py::str("ascii"), py::str("strict"))),
                         py::kwargs());
        FAIL() << "Expected UnicodeDecodeError to be thrown";
    } catch (py::error_already_set& e) {
        EXPECT_STREQ(
          e.what(),
          "UnicodeDecodeError: 'ascii' codec can't decode byte 0xa4 in position 4: ordinal not in range(128)");
    }
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorIgnoreAndErrorDontRaiseUnicodeDecodeError)
{
    auto result = api_str_aspect(py::none(),
                                 0,
                                 py::args(py::make_tuple(py::bytes("test\244"), py::str("ascii"), py::str("ignore"))),
                                 py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result[py::slice(0, 4, 1)].cast<string>().c_str(), "test");
    // No exception should be thrown
}

TEST_F(CheckAspectStr, StrWithStrAndEncodingNotAllowed)
{
    try {
        auto result = api_str_aspect(
          py::none(), 0, py::args(py::make_tuple(py::str("test"), py::str("ascii"), py::str("strict"))), py::kwargs());
        FAIL() << "Expected TypeError to be thrown";
    } catch (py::error_already_set& e) {
        EXPECT_STREQ(e.what(), "TypeError: decoding str is not supported");
    }
}

TEST_F(CheckAspectStr, StrWithList)
{
    // create a py::list with an integer and a string
    auto list = py::list();
    list.append(py::int_(42));
    list.append(py::str("foobar"));

    // auto list = py::list(py::int_(1), py::str("foobar"));
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(list)), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "[42, 'foobar']");
}

TEST_F(CheckAspectStr, StrWithNone)
{
    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(py::none())), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "None");
}

TEST_F(CheckAspectStr, StrWithDict)
{
    auto dict = py::dict();
    dict["key1"] = py::int_(42);
    dict["key2"] = py::str("foobar");

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(dict)), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "{'key1': 42, 'key2': 'foobar'}");
}

TEST_F(CheckAspectStr, StrWithTuple)
{
    auto tuple = py::tuple(py::make_tuple(py::str("foo"), py::str("bar")));

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(tuple)), py::kwargs());
    EXPECT_TRUE(py::isinstance<py::str>(result));
    EXPECT_STREQ(result.cast<string>().c_str(), "('foo', 'bar')");
}

TEST_F(CheckAspectStr, StrWithTaintedStringNoEncoding)
{
    auto str = py::str("example");
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 7;
    taint_range->source.name = "example";
    api_set_ranges(str, TaintRangeRefs{ taint_range });
    auto ranges = api_get_ranges(str);
    EXPECT_EQ(ranges.size(), 1);

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(str)), py::kwargs());
    auto ranges2 = api_get_ranges(result);
    EXPECT_RANGESEQ(ranges, ranges2);
}

TEST_F(CheckAspectStr, StrWithTaintedBytesNoEncoding)
{
    auto bytes = py::bytes("example");
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 7;
    taint_range->source.name = "example";
    api_set_ranges(bytes, TaintRangeRefs{ taint_range });
    auto ranges = api_get_ranges(bytes);
    EXPECT_EQ(ranges.size(), 1);

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(bytes)), py::kwargs());
    EXPECT_STREQ(result.cast<string>().c_str(), "b'example'");
    auto ranges2 = api_get_ranges(result);
    EXPECT_EQ(ranges2.size(), 1);
    EXPECT_EQ(ranges2[0]->start, 0);
    EXPECT_EQ(ranges2[0]->length, 10);
}

TEST_F(CheckAspectStr, StrWithTaintedByteArrayNoEncoding)
{
    auto bytearray = py::bytearray("example");
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 7;
    taint_range->source.name = "example";
    api_set_ranges(bytearray, TaintRangeRefs{ taint_range });
    auto ranges = api_get_ranges(bytearray);
    EXPECT_EQ(ranges.size(), 1);

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(bytearray)), py::kwargs());
    EXPECT_STREQ(result.cast<string>().c_str(), "bytearray(b'example')");
    auto ranges2 = api_get_ranges(result);
    EXPECT_EQ(ranges2.size(), 1);
    EXPECT_EQ(ranges2[0]->start, 0);
    EXPECT_EQ(ranges2[0]->length, 21);
}

TEST_F(CheckAspectStr, StrWithTaintedBytesAndEncodingSameSize)
{
    auto bytes = py::bytes("example");
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 7;
    taint_range->source.name = "example";
    api_set_ranges(bytes, TaintRangeRefs{ taint_range });
    auto ranges = api_get_ranges(bytes);
    EXPECT_EQ(ranges.size(), 1);

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(bytes, py::str("utf-8"))), py::kwargs());
    EXPECT_STREQ(result.cast<string>().c_str(), "example");
    auto ranges2 = api_get_ranges(result);
    EXPECT_RANGESEQ(ranges, ranges2);
}

TEST_F(CheckAspectStr, StrWithTaintedBytesAndEncodingDifferentSize)
{
    const char utf16le_example[] = {
        '\x65', '\x00', // 'e'
        '\x78', '\x00', // 'x'
        '\x61', '\x00', // 'a'
        '\x6D', '\x00', // 'm'
        '\x70', '\x00', // 'p'
        '\x6C', '\x00', // 'l'
        '\x65', '\x00'  // 'e'
    };
    auto bytes = py::bytes(utf16le_example, sizeof(utf16le_example));
    TaintRangePtr taint_range = std::make_shared<TaintRange>();
    taint_range->start = 0;
    taint_range->length = 14;
    taint_range->source.name = "example";
    api_set_ranges(bytes, TaintRangeRefs{ taint_range });
    auto ranges = api_get_ranges(bytes);
    EXPECT_EQ(ranges.size(), 1);

    auto result = api_str_aspect(py::none(), 0, py::args(py::make_tuple(bytes, py::str("utf-16"))), py::kwargs());
    EXPECT_STREQ(result.cast<string>().c_str(), "example");
    auto ranges2 = api_get_ranges(result);
    EXPECT_EQ(ranges2.size(), 1);
    EXPECT_EQ(ranges2[0]->start, 0);
    EXPECT_EQ(ranges2[0]->length, 7);
}