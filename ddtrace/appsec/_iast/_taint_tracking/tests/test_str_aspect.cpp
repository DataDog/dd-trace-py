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
        cerr << "JJJ fucking result: " << result << endl;
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

// TODO: more tests:
// - Propagation, including the fallback cases.
// - Other random argument types