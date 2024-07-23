/**
 * IAST Native Module
 * This C++ module contains IAST propagation features:
 * - Taint tracking: propagation of a tainted variable.
 * - Aspects: common string operations are replaced by functions that propagate the taint variables.
 * - Taint ranges: Information related to tainted values.
 */
#include <memory>
#include <pybind11/pybind11.h>

#include "Aspects/AspectExtend.h"
#include "Aspects/AspectIndex.h"
#include "Aspects/AspectJoin.h"
#include "Aspects/AspectOperatorAdd.h"
#include "Aspects/AspectSlice.h"
#include "Aspects/_aspects_exports.h"
#include "Constants.h"
#include "Initializer/_initializer.h"
#include "TaintTracking/_taint_tracking.h"
#include "TaintedOps/TaintedOps.h"

#define PY_MODULE_NAME_ASPECTS                                                                                         \
    PY_MODULE_NAME "."                                                                                                 \
                   "aspects"

using namespace pybind11::literals;
namespace py = pybind11;

static PyMethodDef AspectsMethods[] = {
    { "add_aspect", ((PyCFunction)api_add_aspect), METH_FASTCALL, "aspect add" },
    { "extend_aspect", ((PyCFunction)api_extend_aspect), METH_FASTCALL, "aspect extend" },
    { "index_aspect", ((PyCFunction)api_index_aspect), METH_FASTCALL, "aspect index" },
    { "join_aspect", ((PyCFunction)api_join_aspect), METH_FASTCALL, "aspect join" },
    { "slice_aspect", ((PyCFunction)api_slice_aspect), METH_FASTCALL, "aspect slice" },
    { nullptr, nullptr, 0, nullptr }
};

static struct PyModuleDef aspects = { PyModuleDef_HEAD_INIT,
                                      .m_name = PY_MODULE_NAME_ASPECTS,
                                      .m_doc = "Taint tracking Aspects",
                                      .m_size = -1,
                                      .m_methods = AspectsMethods };

static PyMethodDef OpsMethods[] = {
    { "new_pyobject_id", (PyCFunction)api_new_pyobject_id, METH_FASTCALL, "new pyobject id" },
    { "set_ranges_from_values", ((PyCFunction)api_set_ranges_from_values), METH_FASTCALL, "set_ranges_from_values" },
    { nullptr, nullptr, 0, nullptr }
};

static struct PyModuleDef ops = { PyModuleDef_HEAD_INIT,
                                  .m_name = PY_MODULE_NAME_ASPECTS,
                                  .m_doc = "Taint tracking operations",
                                  .m_size = -1,
                                  .m_methods = OpsMethods };

/**
 * This function initializes the native module.
 */
PYBIND11_MODULE(_native, m)
{
    const char* env_iast_enabled = std::getenv("DD_IAST_ENABLED");
    if (env_iast_enabled == nullptr) {
        py::module::import("logging").attr("warning")("IAST not enabled but native module is being loaded");
    } else {
        std::string iast_enabled = std::string(env_iast_enabled);
        std::transform(iast_enabled.begin(), iast_enabled.end(), iast_enabled.begin(), [](unsigned char c) {
            return std::tolower(c);
        });
        if (iast_enabled != "true" && iast_enabled != "1") {
            py::module::import("logging").attr("warning")("IAST not enabled but native module is being loaded");
        }
    }

    initializer = make_unique<Initializer>();
    initializer->create_context();

    m.doc() = "Native Python module";

    py::module m_initializer = pyexport_m_initializer(m);
    pyexport_m_taint_tracking(m);

    pyexport_m_aspect_helpers(m);

    // Note: the order of these definitions matter. For example,
    // stacktrace_element definitions must be before the ones of the
    // classes inheriting from it.
    PyObject* hm_aspects = PyModule_Create(&aspects);
    m.add_object("aspects", hm_aspects);

    PyObject* hm_ops = PyModule_Create(&ops);
    m.add_object("ops", hm_ops);
}