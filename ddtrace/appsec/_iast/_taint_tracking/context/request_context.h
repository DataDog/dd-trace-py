#pragma once
#include <pybind11/pybind11.h>
#include <string>

// Global native ContextVar for IAST context id, created in native.cpp
extern "C" {
    extern PyObject* g_iast_ctxvar; // strong ref owned in native.cpp
    void ensure_iast_ctxvar_created();
}

namespace py = pybind11;

// Returns true if the current ContextVar value is a unicode string and sets out ptr/len
// to its UTF-8 view (borrowed). Caller must copy if they need to persist beyond this call.
inline bool get_current_context_id_utf8(const char** out_ptr, Py_ssize_t* out_len)
{
    if (!g_iast_ctxvar) {
        ensure_iast_ctxvar_created();
        if (!g_iast_ctxvar) {
            return false;
        }
    }
    py::gil_scoped_acquire gil;
    PyObject* value = nullptr;
    if (PyContextVar_Get(g_iast_ctxvar, Py_None, &value) != 0) {
        // Error retrieving; clear and return false
        Py_XDECREF(value);
        PyErr_Clear();
        return false;
    }
    if (!value || value == Py_None) {
        Py_XDECREF(value);
        return false;
    }
    if (!PyUnicode_Check(value)) {
        Py_DECREF(value);
        return false;
    }
    *out_ptr = PyUnicode_AsUTF8AndSize(value, out_len);
    Py_DECREF(value);
    return *out_ptr != nullptr && *out_len >= 0;
}

inline std::string get_context_id_from_python()
{
    if (!g_iast_ctxvar) {
        return std::string();
    }
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    if (get_current_context_id_utf8(&ptr, &len)) {
        return std::string(ptr, static_cast<size_t>(len));
    }
    return std::string();
}
