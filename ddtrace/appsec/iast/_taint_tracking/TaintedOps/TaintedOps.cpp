#include "TaintedOps.h"

typedef struct _PyASCIIObject_State_Hidden
{
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

PyObject*
new_pyobject_id(PyObject* tainted_object, Py_ssize_t object_length)
{
    PyObject* empty_unicode = PyUnicode_New(0, 127);
    if (PyUnicode_Check(tainted_object)) {
        //        if (PyUnicode_CHECK_INTERNED(tainted_object) == 0) { //
        //        SSTATE_NOT_INTERNED
        //            Py_INCREF(tainted_object);
        //            return tainted_object;
        //        }
        return PyUnicode_Join(empty_unicode, Py_BuildValue("(OO)", tainted_object, empty_unicode));
    }
    if (PyBytes_Check(tainted_object)) {
        auto tainted_object_ob = py::reinterpret_borrow<py::object>(tainted_object);
        // py::bytes result_ptr = empty_bytes_join(tainted_object_ob);
        return tainted_object_ob.ptr();
    } else if (PyByteArray_Check(tainted_object)) {
        auto tainted_object_ob = py::reinterpret_borrow<py::object>(tainted_object);
        // py::bytearray result_ptr = empty_bytearray.attr("join")(py::reinterpret_borrow<py::object>(tainted_object));
        return tainted_object_ob.ptr();
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
