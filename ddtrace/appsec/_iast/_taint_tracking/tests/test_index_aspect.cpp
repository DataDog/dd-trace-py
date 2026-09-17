#include "aspects/helpers.h"

#include <api/safe_initializer.h>
#include <aspects/aspect_index.h>
#include <pybind11/pybind11.h>
#include <tests/test_common.hpp>

using AspectIndexCheck = PyEnvWithContext;

TEST_F(AspectIndexCheck, check_index_internal_all_nullptr)
{
    const TaintRangeRefs refs;
    const auto tx_map = safe_get_tainted_object_map_by_ctx_id(context_id.value());
    index_aspect(nullptr, nullptr, nullptr, refs, tx_map);
}

TEST_F(AspectIndexCheck, check_index_internal_all_nullptr_negative_index)
{
    PyObject* idx = PyLong_FromLong(-1);
    const TaintRangeRefs refs;
    const auto tx_map = safe_get_tainted_object_map_by_ctx_id(context_id.value());
    auto ret = index_aspect(nullptr, nullptr, idx, refs, tx_map);
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

// Regression: an unhashable object whose address collides with a stale taint-map entry used to
// leave a TypeError set, which CPython then reported as
// "SystemError: <built-in function index_aspect> returned a result with an error set".
// Reproduced here by planting the stale entry, since real collisions depend on the allocator.
TEST_F(AspectIndexCheck, unhashable_object_at_a_stale_map_address_leaves_no_error)
{
    const auto tx_map = safe_get_tainted_object_map_by_ctx_id(context_id.value());
    ASSERT_NE(tx_map, nullptr);

    // A dict is unhashable, like the http.cookies.Morsel values this was found with.
    const py::dict unhashable;
    tx_map->insert({ reinterpret_cast<uintptr_t>(unhashable.ptr()),
                     std::make_pair(Py_hash_t{ 1234 }, safe_allocate_tainted_object()) });

    const auto tainted = get_tainted_object(unhashable.ptr(), tx_map);

    EXPECT_EQ(tainted, nullptr);
    EXPECT_EQ(PyErr_Occurred(), nullptr) << "hashing an unhashable object must not leave an error set";
}

TEST_F(AspectIndexCheck, index_aspect_on_a_dict_holding_an_unhashable_value_returns_the_value)
{
    const auto tx_map = safe_get_tainted_object_map_by_ctx_id(context_id.value());
    ASSERT_NE(tx_map, nullptr);

    // What Django's set_cookie does: subscript a dict subclass whose values are unhashable.
    py::dict container;
    const py::str key("cookie");
    const py::dict value;
    container[key] = value;
    tx_map->insert(
      { reinterpret_cast<uintptr_t>(value.ptr()), std::make_pair(Py_hash_t{ 1234 }, safe_allocate_tainted_object()) });

    PyObject* args_array[2];
    args_array[0] = container.ptr();
    args_array[1] = key.ptr();
    const auto result = py::reinterpret_steal<py::object>(api_index_aspect(nullptr, args_array, 2));

    EXPECT_EQ(result.ptr(), value.ptr());
    EXPECT_EQ(PyErr_Occurred(), nullptr) << "a stale entry must not turn a successful subscript into an error";
}
