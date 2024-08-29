#pragma once

#include "Helpers.h"

template<class StrType>
py::list
api_splitlines_text(const py::object& orig_function,
                 int flag_added_args,
                 const py::args& args,
                 const py::kwargs& kwargs);

void
pyexport_aspect_split(py::module& m);