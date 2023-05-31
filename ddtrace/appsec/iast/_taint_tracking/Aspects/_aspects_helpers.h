#pragma once
#include <pybind11/pybind11.h>
#include "Helpers.h"

inline void
pyexport_m_aspect_helpers(py::module& m)
{
    py::module m_aspect_helpers = m.def_submodule("aspect_helpers", "Aspect Helpers");
    pyexport_aspect_helpers(m_aspect_helpers);
}
