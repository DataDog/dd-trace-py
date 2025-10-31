#include "aspects/helpers.h"

#include <aspects/aspect_modulo.h>
#include <initializer/initializer.h>
#include <tests/test_common.hpp>

using AspectModuloCheck = PyEnvWithContext;

TEST_F(AspectModuloCheck, check_api_modulo_aspect_string_positional)
{
    // template: "hello %s", arg: "world" -> "hello world"
    PyObject* candidate_text = this->StringToPyObjectStr("hello %s");
    PyObject* param = this->StringToPyObjectStr("world");

    // Precompute native result (borrow to API, but keep reference mgmt here)
    PyObject* candidate_result = PyNumber_Remainder(candidate_text, param);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = param;            // non-tuple; API will pack when needed
    args_array[2] = candidate_result; // borrowed by API; it will INCREF before returning

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);
    EXPECT_EQ(result_string, "hello world");

    Py_DecRef(candidate_text);
    Py_DecRef(param);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_float_parameter)
{
    // template: "%0.2f", arg: 3.14159 -> "3.14"
    PyObject* candidate_text = this->StringToPyObjectStr("%0.2f");
    PyObject* param = PyFloat_FromDouble(3.14159);

    PyObject* candidate_result = PyNumber_Remainder(candidate_text, param);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = param;            // non-tuple
    args_array[2] = candidate_result; // borrowed

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);
    EXPECT_EQ(result_string, "3.14");

    Py_DecRef(candidate_text);
    Py_DecRef(param);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_float_parameter_with_tainted_template)
{
    // Create a context and taint the template prefix to simulate tainted template without percent in the tainted range
    this->context_id = taint_engine_context->start_request_context();
    ASSERT_TRUE(this->context_id.has_value());
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(this->context_id.value());

    PyObject* candidate_text = this->StringToPyObjectStr("template %0.2f");
    PyObject* param = PyFloat_FromDouble(3.14159);

    // Taint the word "template" (positions 0..8)
    auto source = Source("input1", "template", OriginType::PARAMETER);
    auto range = initializer->allocate_taint_range(0, 8, source, {});
    TaintRangeRefs ranges{ range };
    ASSERT_TRUE(set_ranges(candidate_text, ranges, tx_map));

    PyObject* candidate_result = PyNumber_Remainder(candidate_text, param);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = param;
    args_array[2] = candidate_result;

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);
    EXPECT_EQ(result_string, "template 3.14");

    Py_DecRef(candidate_text);
    Py_DecRef(param);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_string_tuple)
{
    // template: "%s-%s", args: ("a", "b") -> "a-b"
    PyObject* candidate_text = this->StringToPyObjectStr("%s-%s");
    PyObject* a = this->StringToPyObjectStr("a");
    PyObject* b = this->StringToPyObjectStr("b");

    PyObject* tuple = PyTuple_Pack(2, a, b);
    ASSERT_TRUE(tuple != nullptr);

    PyObject* candidate_result = PyNumber_Remainder(candidate_text, tuple);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = tuple;            // tuple as-is
    args_array[2] = candidate_result; // borrowed

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);
    EXPECT_EQ(result_string, "a-b");

    Py_DecRef(candidate_text);
    Py_DecRef(a);
    Py_DecRef(b);
    Py_DecRef(tuple);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_string_mapping)
{
    // template: "%(name)s", args: {"name": "Alice"} -> "Alice"
    PyObject* candidate_text = this->StringToPyObjectStr("%(name)s");
    PyObject* key = this->StringToPyObjectStr("name");
    PyObject* value = this->StringToPyObjectStr("Alice");

    PyObject* dict = PyDict_New();
    ASSERT_TRUE(dict != nullptr);
    ASSERT_EQ(PyDict_SetItem(dict, key, value), 0);

    PyObject* candidate_result = PyNumber_Remainder(candidate_text, dict);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = dict;             // mapping as-is
    args_array[2] = candidate_result; // borrowed

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectStrToString(result);
    EXPECT_EQ(result_string, "Alice");

    Py_DecRef(candidate_text);
    Py_DecRef(key);
    Py_DecRef(value);
    Py_DecRef(dict);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_bytes_positional)
{
    // template: b"%s", arg: b"abc" -> b"abc"
    PyObject* candidate_text = this->StringToPyObjectBytes("%s");
    PyObject* param = this->StringToPyObjectBytes("abc");

    PyObject* candidate_result = PyNumber_Remainder(candidate_text, param);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = candidate_text;
    args_array[1] = param;
    args_array[2] = candidate_result;

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    std::string result_string = this->PyObjectBytesToString(result);
    EXPECT_EQ(result_string, "abc");

    Py_DecRef(candidate_text);
    Py_DecRef(param);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}

TEST_F(AspectModuloCheck, check_api_modulo_aspect_numeric_passthrough)
{
    // Non-text operands should be passed through untouched; 7 % 3 == 1
    PyObject* lhs = PyLong_FromLong(7);
    PyObject* rhs = PyLong_FromLong(3);

    PyObject* candidate_result = PyNumber_Remainder(lhs, rhs);
    ASSERT_TRUE(candidate_result != nullptr);

    PyObject* args_array[3];
    args_array[0] = lhs;
    args_array[1] = rhs;
    args_array[2] = candidate_result;

    PyObject* result = api_modulo_aspect(nullptr, args_array, 3);
    EXPECT_FALSE(has_pyerr());

    long value = PyLong_AsLong(result);
    EXPECT_EQ(value, 1);

    Py_DecRef(lhs);
    Py_DecRef(rhs);
    Py_DecRef(candidate_result);
    Py_DecRef(result);
}
