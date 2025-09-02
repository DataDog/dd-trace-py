#pragma once
#include <pybind11/pybind11.h>

#include "context/application_context.h"

inline py::module
pyexport_m_application_context(py::module &m)
{
    // ApplicationContext
    py::module m_context = m.def_submodule("context", "Application Context");
    pyexport_application_context(m_context);
    return m_context;
}
