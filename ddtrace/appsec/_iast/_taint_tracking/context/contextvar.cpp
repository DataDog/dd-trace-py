#include <pybind11/pybind11.h>
#include "context/request_context.h"

namespace py = pybind11;

extern "C" void ensure_iast_ctxvar_created() {
    if (g_iast_ctxvar) {
        return;
    }
    py::gil_scoped_acquire gil;
    // Try to reuse a global from builtins to share between module and test exe
    PyObject* builtins = PyEval_GetBuiltins();
    if (builtins && PyDict_Check(builtins)) {
        PyObject* existing = PyDict_GetItemString(builtins, "__dd_iast_ctxvar__"); // borrowed
        if (existing) {
            g_iast_ctxvar = existing;
            Py_INCREF(g_iast_ctxvar);
            return;
        }
    }
    // Create and store in builtins
    PyObject* ctx = PyContextVar_New("IAST_CONTEXT", nullptr);
    if (ctx) {
        g_iast_ctxvar = ctx; // owns ref
        if (builtins && PyDict_Check(builtins)) {
            PyDict_SetItemString(builtins, "__dd_iast_ctxvar__", ctx);
        }
    } else {
        PyErr_Clear();
    }
}
