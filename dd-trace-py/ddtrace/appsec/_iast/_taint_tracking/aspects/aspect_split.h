#pragma once
#include "api/safe_context.h"
#include "api/safe_initializer.h"
#include "api/utils.h"
#include "helpers.h"
#include "tainted_ops/tainted_ops.h"

py::object
api_splitlines_text(const py::object& orig_function,
                    int flag_added_args,
                    const py::args& args,
                    const py::kwargs& kwargs);

void
pyexport_aspect_split(py::module& m);
