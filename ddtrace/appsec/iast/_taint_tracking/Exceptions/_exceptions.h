#pragma once
#include <pybind11/pybind11.h>

#include "Exceptions/exceptions.h"

namespace py = pybind11;

inline void
pyexport_m_exceptions(py::module& m, py::module& m_initializer)
{
    // Exceptions
    py::register_exception<DatadogNativeException>(m, "DatadogNativeException");

    py::register_exception<ContextNotInitializedException>(m_initializer, "ContextNotInitializedException");
}
