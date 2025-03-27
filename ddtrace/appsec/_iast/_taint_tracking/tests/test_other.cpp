#include <Python.h>
#include <gtest/gtest.h>
#include <tests/test_common.hpp>

using PyByteArray_Check = PyEnvCheck;

TEST_F(PyByteArray_Check, WithOtherTypes)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    EXPECT_FALSE(PyByteArray_Check(non_text_obj));
    // Test with None
    PyObject* none_obj = Py_None;
    Py_INCREF(none_obj);
    EXPECT_FALSE(PyByteArray_Check(none_obj)) << "Failed for None type";
    Py_DECREF(none_obj);

    // Test with Bool
    PyObject* bool_obj = Py_True;
    Py_INCREF(bool_obj);
    EXPECT_FALSE(PyByteArray_Check(bool_obj)) << "Failed for Bool type";
    Py_DECREF(bool_obj);

    // Test with Integer
    PyObject* int_obj = PyLong_FromLong(42);
    EXPECT_FALSE(PyByteArray_Check(int_obj)) << "Failed for Integer type";
    Py_DECREF(int_obj);

    // Test with Float
    PyObject* float_obj = PyFloat_FromDouble(3.14);
    EXPECT_FALSE(PyByteArray_Check(float_obj)) << "Failed for Float type";
    Py_DECREF(float_obj);

    // Test with String
    PyObject* str_obj = PyUnicode_FromString("test");
    EXPECT_FALSE(PyByteArray_Check(str_obj)) << "Failed for String type";
    Py_DECREF(str_obj);

    // Test with Bytes
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_FALSE(PyByteArray_Check(bytes_obj)) << "Failed for Bytes type";
    Py_DECREF(bytes_obj);

    // Test with Tuple
    PyObject* tuple_obj = PyTuple_New(0);
    EXPECT_FALSE(PyByteArray_Check(tuple_obj)) << "Failed for Tuple type";
    Py_DECREF(tuple_obj);

    // Test with List
    PyObject* list_obj = PyList_New(0);
    EXPECT_FALSE(PyByteArray_Check(list_obj)) << "Failed for List type";
    Py_DECREF(list_obj);

    // Test with Dict
    PyObject* dict_obj = PyDict_New();
    EXPECT_FALSE(PyByteArray_Check(dict_obj)) << "Failed for Dict type";
    Py_DECREF(dict_obj);

    // Test with Set
    PyObject* set_obj = PySet_New(NULL);
    EXPECT_FALSE(PyByteArray_Check(set_obj)) << "Failed for Set type";
    Py_DECREF(set_obj);

    PyObject* globals = PyDict_New();
    PyObject* locals = PyDict_New();
    if (PY_VERSION_HEX >= 0x03080000) {
        // PyByteArray_Check is bugged for user-defined classes in Python 3.7
        // Test with a user-defined class
        PyRun_String("class TestClass:\n    pass\n\ntest_instance = TestClass()", Py_file_input, globals, locals);
        PyObject* user_obj = PyDict_GetItemString(locals, "test_instance");
        EXPECT_FALSE(PyByteArray_Check(user_obj)) << "Failed for user-defined class";
    }

    // Test with actual ByteArray (this should return true)
    PyObject* bytearray_obj = PyByteArray_FromStringAndSize("test", 4);
    EXPECT_TRUE(PyByteArray_Check(bytearray_obj)) << "Failed for actual ByteArray type";
    Py_DECREF(bytearray_obj);

    // Test with Complex number
    PyObject* complex_obj = PyComplex_FromDoubles(1.0, 2.0);
    EXPECT_FALSE(PyByteArray_Check(complex_obj)) << "Failed for Complex type";
    Py_DECREF(complex_obj);

    // Test with Function
    PyRun_String("def test_function():\n    pass", Py_file_input, globals, locals);
    PyObject* func_obj = PyDict_GetItemString(locals, "test_function");
    EXPECT_FALSE(PyByteArray_Check(func_obj)) << "Failed for Function type";
    Py_DECREF(globals);
    Py_DECREF(locals);
}
