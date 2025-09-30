#include "tainted_ops.h"
#include "taint_tracking/taint_range.h"

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
is_tainted(PyObject* tainted_object, const TaintedObjectMapTypePtr& tx_taint_map)
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
        const auto& to_initial =
          taint_engine_context->get_tainted_object_from_request_context_slot(tainted_object.ptr());
        if (to_initial and !to_initial->get_ranges().empty()) {
            return true;
        }
    }

    return false;
}

void
pyexport_tainted_ops(py::module& m)
{
    m.def("is_tainted", &api_is_tainted, "tainted_object"_a, py::return_value_policy::move);
}
