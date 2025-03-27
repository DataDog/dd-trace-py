#include <aspects/aspect_str.h>
#include <aspects/helpers.h>
#include <iostream>
#include <tests/test_common.hpp>

using CheckAspectStr = PyEnvWithContext;

static py::object
str_func()
{
    return safe_import("builtins", "str");
}

static PyObject**
one_arg(py::object value)
{
    PyObject** args = new PyObject*[3];
    args[0] = str_func().release().ptr();
    args[1] = PyLong_FromLong(0);
    args[2] = value.ptr();
    return args;
}

static PyObject**
two_args(py::object value, py::object value2)
{
    PyObject** args = new PyObject*[4];
    args[0] = str_func().release().ptr();
    args[1] = PyLong_FromLong(0);
    args[2] = value.ptr();
    args[3] = value2.ptr();
    return args;
}

static PyObject**
three_args(py::object value, py::object value2, py::object value3)
{
    PyObject** args = new PyObject*[5];
    args[0] = str_func().release().ptr();
    args[1] = PyLong_FromLong(0);
    args[2] = value.ptr();
    args[3] = value2.ptr();
    args[4] = value3.ptr();
    return args;
}

TEST_F(CheckAspectStr, StrWithStr)
{
    // auto result = api_str_aspect(py::none().ptr(), one_arg(py::str("test")), 3, py::none().ptr());
    auto result = api_str_aspect(py::none().ptr(), one_arg(py::str("test")), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "test");
}

TEST_F(CheckAspectStr, StrWithInteger)
{
    auto result = api_str_aspect(py::none().ptr(), one_arg(py::int_(42)), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "42");
}

TEST_F(CheckAspectStr, StrWithFloat)
{
    auto result = api_str_aspect(py::none().ptr(), one_arg(py::float_(42.42)), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "42.42");
}

TEST_F(CheckAspectStr, StrWithBytesNoEncoding)
{
    auto result = api_str_aspect(py::none().ptr(), one_arg(py::bytes("test")), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "b'test'");
}

TEST_F(CheckAspectStr, StrWithBytesAndEncoding)
{
    auto result = api_str_aspect(py::none().ptr(), two_args(py::bytes("test"), py::str("utf-8")), 4, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "test");
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorStrictButNoError)
{
    auto result = api_str_aspect(
      py::none().ptr(), three_args(py::bytes("test"), py::str("utf-8"), py::str("strict")), 5, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "test");
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorStrictAndErrorRaisesUnicodeDecodeError)
{
    auto result = api_str_aspect(
      py::none().ptr(), three_args(py::bytes("test\244"), py::str("ascii"), py::str("strict")), 5, py::none().ptr());
    EXPECT_EQ(result, nullptr);
    auto error = has_pyerr_as_string();
    EXPECT_STREQ(error.c_str(), "'ascii' codec can't decode byte 0xa4 in position 4: ordinal not in range(128)");
}

TEST_F(CheckAspectStr, StrWithBytesAndErrorIgnoreAndErrorDontRaiseUnicodeDecodeError)
{
    auto result = api_str_aspect(
      py::none().ptr(), three_args(py::bytes("test\244"), py::str("ascii"), py::str("ignore")), 5, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    auto presult = py::reinterpret_borrow<py::str>(result);
    EXPECT_STREQ(presult[py::slice(0, 4, 1)].cast<string>().c_str(), "test");
    // No exception should be thrown
}

TEST_F(CheckAspectStr, StrWithList)
{
    // create a py::list with an integer and a string
    auto list = py::list();
    list.append(py::int_(42));
    list.append(py::str("foobar"));

    // auto list = py::list(py::int_(1), py::str("foobar"));
    auto result = api_str_aspect(py::none().ptr(), one_arg(list), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "[42, 'foobar']");
}

TEST_F(CheckAspectStr, StrWithNone)
{
    auto result = api_str_aspect(py::none().ptr(), one_arg(py::none()), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "None");
}

TEST_F(CheckAspectStr, StrWithDict)
{
    auto dict = py::dict();
    dict["key1"] = py::int_(42);
    dict["key2"] = py::str("foobar");

    auto result = api_str_aspect(py::none().ptr(), one_arg(dict), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "{'key1': 42, 'key2': 'foobar'}");
}

TEST_F(CheckAspectStr, StrWithTuple)
{
    auto tuple = py::tuple(py::make_tuple(py::str("foo"), py::str("bar")));

    auto result = api_str_aspect(py::none().ptr(), one_arg(tuple), 3, py::none().ptr());
    EXPECT_TRUE(PyUnicode_Check(result));
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "('foo', 'bar')");
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

    auto result = api_str_aspect(py::none().ptr(), one_arg(str), 3, py::none().ptr());
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
    auto result = api_str_aspect(py::none().ptr(), one_arg(bytes), 3, py::none().ptr());
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "b'example'");
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

    auto result = api_str_aspect(py::none().ptr(), one_arg(bytearray), 3, py::none().ptr());
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "bytearray(b'example')");
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

    auto result = api_str_aspect(py::none().ptr(), two_args(bytes, py::str("utf-8")), 4, py::none().ptr());
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "example");
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

    auto result = api_str_aspect(py::none().ptr(), two_args(bytes, py::str("utf-16")), 4, py::none().ptr());
    EXPECT_STREQ(PyUnicode_AsUTF8(result), "example");
    auto ranges2 = api_get_ranges(result);
    EXPECT_EQ(ranges2.size(), 1);
    EXPECT_EQ(ranges2[0]->start, 0);
    EXPECT_EQ(ranges2[0]->length, 7);
}
