#pragma once
#include "Aspects/Helpers.h"

py::str
api_str_aspect(const py::object& orig_function, int flag_added_args, const py::args& args, const py::kwargs& kwargs);

void
pyexport_aspect_str(py::module& m);
