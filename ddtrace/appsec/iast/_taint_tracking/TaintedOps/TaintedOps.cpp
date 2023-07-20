#include "TaintedOps.h"
#include <iostream> // JJJ remove

PyObject* bytes_join = NULL;
PyObject* bytearray_join = NULL;
PyObject* empty_bytes = NULL;
PyObject* empty_bytearray = NULL;
PyObject* empty_unicode = NULL;

typedef struct _PyASCIIObject_State_Hidden
{
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

// TODO: detect when this has not been called but one of the functions below has been
PyObject*
setup(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyArg_ParseTuple(args, "OO", &bytes_join, &bytearray_join);
    empty_bytes = PyBytes_FromString("");
    empty_bytearray = PyByteArray_FromObject(empty_bytes);
    empty_unicode = PyUnicode_New(0, 127);
    Py_RETURN_NONE;
}

PyObject*
new_pyobject_id(PyObject* tainted_object, Py_ssize_t object_length)
{
    if (PyUnicode_Check(tainted_object)) {
        //        if (PyUnicode_CHECK_INTERNED(tainted_object) == 0) { //
        //        SSTATE_NOT_INTERNED
        //            Py_INCREF(tainted_object);
        //            return tainted_object;
        //        }
        return PyUnicode_Join(empty_unicode, Py_BuildValue("(OO)", tainted_object, empty_unicode));
    }
    if (PyBytes_Check(tainted_object)) {
        return PyObject_CallFunctionObjArgs(
          bytes_join, empty_bytes, Py_BuildValue("(OO)", tainted_object, empty_bytes), NULL);
    } else if (PyByteArray_Check(tainted_object)) {
        return PyObject_CallFunctionObjArgs(
          bytearray_join, empty_bytearray, Py_BuildValue("(OO)", tainted_object, empty_bytearray), NULL);
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
    if (to_initial and to_initial->get_ranges().size()) {
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
