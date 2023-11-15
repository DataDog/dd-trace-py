#pragma once
#include <pybind11/pybind11.h>

#include "Initializer/Initializer.h"

inline py::module
pyexport_m_initializer(py::module& m)
{
    // Initializer
    py::module m_initializer = m.def_submodule("initializer", "Initializer");
    pyexport_initializer(m_initializer);
    return m_initializer;
}
