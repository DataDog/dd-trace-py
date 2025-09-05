#pragma once
#include <pybind11/pybind11.h>

#include "context/taint_engine_context.h"

inline py::module
pyexport_m_taint_engine_context(py::module& m)
{
    // TaintEngineContext
    py::module m_context = m.def_submodule("context", "Application Context");
    pyexport_taint_engine_context(m_context);
    return m_context;
}
