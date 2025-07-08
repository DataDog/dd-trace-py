#pragma once
#include <pybind11/pybind11.h>

inline void
pyexport_m_taint_tracking(py::module& m)
{
    py::module m_taint_tracking = m.def_submodule("taint_tracking", "Taint Tracking");
    pyexport_source(m_taint_tracking);
    pyexport_taintrange(m_taint_tracking);
    pyexport_taintedobject(m_taint_tracking);
    pyexport_tainted_ops(m_taint_tracking);
}