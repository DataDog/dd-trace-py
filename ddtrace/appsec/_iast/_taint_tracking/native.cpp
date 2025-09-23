/**
 * IAST Native Module
 * This C++ module contains IAST propagation features:
 * - Taint tracking: propagation of a tainted variable.
 * - Aspects: common string operations are replaced by functions that propagate the taint variables.
 * - Taint ranges: Information related to tainted values.
 */
#include <memory>
#include <pybind11/pybind11.h>

#include "aspects/aspect_extend.h"
#include "aspects/aspect_index.h"
#include "aspects/aspect_join.h"
#include "aspects/aspect_modulo.h"
#include "aspects/aspect_operator_add.h"
#include "aspects/aspect_slice.h"
#include "aspects/aspect_str.h"
#include "aspects/aspects_exports.h"
#include "constants.h"
#include "context/_taint_engine_context.h"
#include "taint_tracking/taint_tracking.h"
#include "tainted_ops/tainted_ops.h"
#include "utils/generic_utils.h"

#define PY_MODULE_NAME_ASPECTS                                                                                         \
    PY_MODULE_NAME "."                                                                                                 \
                   "aspects"

using namespace pybind11::literals;
namespace py = pybind11;

static PyMethodDef AspectsMethods[] = {
    { "add_aspect", ((PyCFunction)api_add_aspect), METH_FASTCALL, "aspect add" },
    { "str_aspect", ((PyCFunction)api_str_aspect), METH_FASTCALL | METH_KEYWORDS, "aspect str" },
    { "add_inplace_aspect", ((PyCFunction)api_add_inplace_aspect), METH_FASTCALL, "aspect add" },
    { "extend_aspect", ((PyCFunction)api_extend_aspect), METH_FASTCALL, "aspect extend" },
    { "index_aspect", ((PyCFunction)api_index_aspect), METH_FASTCALL, "aspect index" },
    { "join_aspect", ((PyCFunction)api_join_aspect), METH_FASTCALL, "aspect join" },
    { "slice_aspect", ((PyCFunction)api_slice_aspect), METH_FASTCALL, "aspect slice" },
    { "modulo_aspect", ((PyCFunction)api_modulo_aspect), METH_FASTCALL, "aspect modulo" },
    { nullptr, nullptr, 0, nullptr }
};

// Mark the module as used to prevent it from being stripped.
static struct PyModuleDef aspects __attribute__((used)) = { PyModuleDef_HEAD_INIT,
                                                            .m_name = PY_MODULE_NAME_ASPECTS,
                                                            .m_doc = "Taint tracking Aspects",
                                                            .m_size = -1,
                                                            .m_methods = AspectsMethods };

static PyMethodDef OpsMethods[] = {
    { "new_pyobject_id", (PyCFunction)api_new_pyobject_id, METH_FASTCALL, "new pyobject id" },
    { "taint_pyobject", ((PyCFunction)api_taint_pyobject), METH_FASTCALL, "api_taint_pyobject" },
    { nullptr, nullptr, 0, nullptr }
};

// Mark the module as used to prevent it from being stripped.
static struct PyModuleDef ops __attribute__((used)) = { PyModuleDef_HEAD_INIT,
                                                        .m_name = PY_MODULE_NAME_ASPECTS,
                                                        .m_doc = "Taint tracking operations",
                                                        .m_size = -1,
                                                        .m_methods = OpsMethods };

/**
 * This function initializes the native module.
 */
PYBIND11_MODULE(_native, m)
{
    initializer = make_unique<Initializer>();
    taint_engine_context = make_unique<TaintEngineContext>();

    // Create a atexit callback to cleanup the Initializer before the interpreter finishes
    auto atexit_register = safe_import("atexit", "register");
    atexit_register(py::cpp_function([]() {
        // During interpreter shutdown (esp. with gevent), heavy cleanup can
        // trigger refcounting or Python API calls without a valid runtime.
        // If gevent monkey-patching is active, skip setting the shutdown flag
        // because it interferes with greenlet scheduling at exit.

        bool gevent_active = false;
        try {
            auto is_patched = safe_import("gevent.monkey", "is_module_patched");
            gevent_active =
              asbool(is_patched("threading")) || asbool(is_patched("socket")) || asbool(is_patched("ssl"));
        } catch (const py::error_already_set&) {
            PyErr_Clear();
        }

        if (!gevent_active) {
            py::gil_scoped_acquire gil; // safe to touch Python-adjacent state
            TaintEngineContext::set_shutting_down(true);
        }

        initializer.reset();
        if (taint_engine_context) {
            taint_engine_context->clear_all_request_context_slots();
            taint_engine_context.reset();
        }
    }));

    m.doc() = "Native Python module";
    py::module m_appctx = pyexport_m_taint_engine_context(m);
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
