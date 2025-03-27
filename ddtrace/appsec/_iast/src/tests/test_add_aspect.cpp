#include "aspects/helpers.h"

#include <aspects/aspect_operator_add.h>
#include <initializer/initializer.h>
#include <tests/test_common.hpp>

using AspectAddCheck = PyEnvWithContext;

TEST_F(AspectAddCheck, check_api_add_aspect_strings_candidate_text_empty)
{
    PyObject* candidate_text = this->StringToPyObjectStr("");
    PyObject* text_to_add = this->StringToPyObjectStr("def");
    PyObject* args_array[2];
    args_array[0] = candidate_text;
    args_array[1] = text_to_add;
    auto result = api_add_aspect(nullptr, args_array, 2);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);

    EXPECT_EQ(result_string, "def");
    Py_DecRef(candidate_text);
    Py_DecRef(text_to_add);
    Py_DecRef(result);
}

TEST_F(AspectAddCheck, check_api_add_aspect_strings_text_to_add_empty)
{
    PyObject* candidate_text = this->StringToPyObjectStr("abc");
    PyObject* text_to_add = this->StringToPyObjectStr("");
    PyObject* args_array[2];
    args_array[0] = candidate_text;
    args_array[1] = text_to_add;
    auto result = api_add_aspect(nullptr, args_array, 2);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);

    EXPECT_EQ(result_string, "abc");
    Py_DecRef(candidate_text);
    Py_DecRef(text_to_add);
    Py_DecRef(result);
}

TEST_F(AspectAddCheck, check_api_add_aspect_strings)
{
    PyObject* candidate_text = this->StringToPyObjectStr("abc");
    PyObject* text_to_add = this->StringToPyObjectStr("def");
    PyObject* args_array[2];
    args_array[0] = candidate_text;
    args_array[1] = text_to_add;
    auto result = api_add_aspect(nullptr, args_array, 2);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);

    EXPECT_EQ(result_string, "abcdef");
    Py_DecRef(candidate_text);
    Py_DecRef(text_to_add);
    Py_DecRef(result);
}

TEST_F(AspectAddCheck, check_api_add_aspect_bytes)
{
    PyObject* candidate_text = this->StringToPyObjectBytes("abc");
    PyObject* text_to_add = this->StringToPyObjectBytes("def");
    PyObject* args_array[2];
    args_array[0] = candidate_text;
    args_array[1] = text_to_add;
    auto result = api_add_aspect(nullptr, args_array, 2);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectBytesToString(result);

    EXPECT_EQ(result_string, "abcdef");
    Py_DecRef(candidate_text);
    Py_DecRef(text_to_add);
    Py_DecRef(result);
}
