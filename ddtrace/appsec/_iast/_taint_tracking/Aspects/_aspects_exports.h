#pragma once
#include "AspectFormat.h"
#include "AspectOsPathJoin.h"
#include "Helpers.h"
#include <pybind11/pybind11.h>

inline void
pyexport_m_aspect_helpers(py::module& m)
{
    py::module m_aspect_helpers = m.def_submodule("aspect_helpers", "Aspect Helpers");
    pyexport_aspect_helpers(m_aspect_helpers);
    py::module m_aspect_format = m.def_submodule("aspect_format", "Aspect Format");
    pyexport_format_aspect(m_aspect_format);
    py::module m_ospath_join = m.def_submodule("aspect_ospath_join", "Aspect os.path.join");
    pyexport_ospathjoin_aspect(m_ospath_join);
}
