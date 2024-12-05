#pragma once

#include "Helpers.h"

py::object
api_lower_text(const py::object& orig_function,
                    int flag_added_args,
                    const py::args& args,
                    const py::kwargs& kwargs);

void
pyexport_aspect_lower(py::module& m);
