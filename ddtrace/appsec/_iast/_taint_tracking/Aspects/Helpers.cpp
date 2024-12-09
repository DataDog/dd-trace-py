#include "Helpers.h"
#include "Initializer/Initializer.h"
#include "Utils/PythonErrorGuard.h"

#include <algorithm>
#include <iostream>

using namespace pybind11::literals;
namespace py = pybind11;


bool
has_pyerr()
{
    PythonErrorGuard error_guard;
    return error_guard.has_error();
}

std::string
has_pyerr_as_string()
{
    PythonErrorGuard error_guard;
    if (not error_guard.has_error()) {
        return {};
    }

    return error_guard.error_as_stdstring();
}

py::str
has_pyerr_as_pystr()
{
    PythonErrorGuard error_guard;
    if (not error_guard.has_error()) {
        return {};
    }

    return error_guard.error_as_pystr();
}

void
pyexport_aspect_helpers(py::module& m)
{
    m.def("has_pyerr", &has_pyerr);
    m.def("has_pyerr_as_string", &has_pyerr_as_string);
}
