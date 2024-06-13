#include "TaintedOps/TaintedOps.h"

PyObject*
api_new_pyobject_id(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 1 or !args) {
        throw py::value_error(MSG_ERROR_N_PARAMS);
    }
    PyObject* tainted_object = args[0];
    return new_pyobject_id(tainted_object);
}

bool
is_tainted(PyObject* tainted_object, const TaintRangeMapTypePtr& tx_taint_map)
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
        const auto tx_map = initializer->get_tainting_map();
        if (not tx_map or tx_map->empty()) {
            return false;
        }

        if (is_tainted(tainted_object.ptr(), tx_map)) {
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
