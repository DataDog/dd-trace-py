#pragma once
#include "initializer/initializer.h"
#include "utils/string_utils.h"

using namespace std;
using namespace pybind11::literals;
namespace py = pybind11;

PyObject*
api_new_pyobject_id(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

bool
is_tainted(PyObject* tainted_object, const TaintRangeMapTypePtr& tx_taint_map);

bool
api_is_tainted(py::object tainted_object);

void
pyexport_tainted_ops(py::module& m);
