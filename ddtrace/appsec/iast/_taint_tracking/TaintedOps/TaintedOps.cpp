#include "TaintedOps/TaintedOps.h"

PyObject*
api_new_pyobject_id(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyObject* tainted_object;
    PyArg_ParseTuple(args, "O", &tainted_object);
    return new_pyobject_id(tainted_object);
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
