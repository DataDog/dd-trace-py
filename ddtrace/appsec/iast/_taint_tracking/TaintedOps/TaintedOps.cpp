#include "TaintedOps.h"

PyObject*
new_pyobject_id(PyObject* tainted_object, Py_ssize_t object_length)
{
    if (PyUnicode_Check(tainted_object)) {
        PyObject* empty_unicode = PyUnicode_New(0, 127);
        PyObject* val = Py_BuildValue("(OO)", tainted_object, empty_unicode);
        PyObject* result = PyUnicode_Join(empty_unicode, val);
        Py_DECREF(empty_unicode);
        Py_DECREF(val);
        return result;
    }
    if (PyBytes_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        auto bytes_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytes).attr("join");
        auto val = Py_BuildValue("(OO)", tainted_object, empty_bytes);
        auto res = PyObject_CallFunctionObjArgs(bytes_join_ptr.ptr(), val, NULL);
        Py_DECREF(val);
        Py_DECREF(empty_bytes);
        return res;
    } else if (PyByteArray_Check(tainted_object)) {
        PyObject* empty_bytes = PyBytes_FromString("");
        PyObject* empty_bytearray = PyByteArray_FromObject(empty_bytes);
        auto bytearray_join_ptr = py::reinterpret_borrow<py::bytes>(empty_bytearray).attr("join");
        auto val = Py_BuildValue("(OO)", tainted_object, empty_bytearray);
        auto res = PyObject_CallFunctionObjArgs(bytearray_join_ptr.ptr(), val, NULL);
        Py_DECREF(val);
        Py_DECREF(empty_bytes);
        Py_DECREF(empty_bytearray);
        return res;
    }
    return tainted_object;
}

PyObject*
api_new_pyobject_id(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyObject* tainted_object;
    Py_ssize_t object_length;
    PyArg_ParseTuple(args, "On", &tainted_object, &object_length);
    return new_pyobject_id(tainted_object, object_length);
}

bool
is_tainted(PyObject* tainted_object, TaintRangeMapType* tx_taint_map)
{
    const auto& to_initial = get_tainted_object(tainted_object, tx_taint_map);
    if (to_initial and !to_initial->get_ranges().empty()) {
        return true;
    }
    return false;
}

bool
api_is_tainted(py::object tainted_object)
{
    if (tainted_object) {
        auto ctx_map = initializer->get_tainting_map();
        if (not ctx_map or ctx_map->empty()) {
            return false;
        }

        if (is_tainted(tainted_object.ptr(), ctx_map)) {
            return true;
        }
    }

    return false;
}

void
pyexport_tainted_ops(py::module& m)
{
    m.def("is_tainted", &api_is_tainted, "tainted_object"_a, py::return_value_policy::move);
    m.def("are_all_text_all_ranges",
          &are_all_text_all_ranges,
          "candidate_text"_a,
          "candidate_text"_a,
          py::return_value_policy::move);
}
