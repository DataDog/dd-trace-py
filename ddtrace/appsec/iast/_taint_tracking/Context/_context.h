#pragma once
#include <pybind11/pybind11.h>

#include "Context.h"

inline void
pyexport_m_context(py::module& m)
{
    py::module m_context = m.def_submodule("context", "Context");

    pyexport_context(m_context);
}
