#pragma once
#include <pybind11/pybind11.h>

inline void
pyexport_m_taint_tracking(py::module& m)
{
    pyexport_source(m);
    pyexport_taintrange(m);
    pyexport_taintedobject(m);
}