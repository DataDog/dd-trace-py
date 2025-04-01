#include "aspects/helpers.h"

#include <aspects/aspect_index.h>
#include <tests/test_common.hpp>

using AspectIndexCheck = PyEnvWithContext;

TEST_F(AspectIndexCheck, check_index_internal_all_nullptr)
{
    const TaintRangeRefs refs;
    index_aspect(nullptr, nullptr, nullptr, refs, Initializer::get_tainting_map());
}

TEST_F(AspectIndexCheck, check_index_internal_all_nullptr_negative_index)
{
    PyObject* idx = PyLong_FromLong(-1);
    const TaintRangeRefs refs;
    auto ret = index_aspect(nullptr, nullptr, idx, refs, Initializer::get_tainting_map());
    EXPECT_EQ(ret, nullptr);
    Py_DecRef(idx);
}

TEST_F(AspectIndexCheck, check_api_index_aspect_all_nullptr)
{
    auto ret = api_index_aspect(nullptr, nullptr, 2);
    EXPECT_EQ(ret, nullptr);
}

TEST_F(AspectIndexCheck, check_api_index_aspect_wrong_index)
{
    PyObject* py_str = PyUnicode_FromString("abc");
    PyObject* idx = PyLong_FromLong(4);
    PyObject* args_array[2];
    args_array[0] = py_str;
    args_array[1] = idx;
    auto res = api_index_aspect(nullptr, args_array, 2);
    ASSERT_EQ(res, nullptr);
    EXPECT_EQ(has_pyerr_as_string(), std::string("string index out of range"));
    PyErr_Clear();
    Py_DecRef(py_str);
    Py_DecRef(idx);
}
