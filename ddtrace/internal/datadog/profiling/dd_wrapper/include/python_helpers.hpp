#pragma once

#include <Python.h>

#include "pymacro.hpp"

/* RAII helper to preserve the raised C-level exception indicator.
 *
 * The allocator hook can run in contexts where CPython already has a raised
 * exception in flight (PyErr_Occurred() != NULL). During sampling, we call
 * C-API functions that may set or clear the indicator on failure.
 *
 * CPython uses this same save/restore pattern in sensitive paths (for example,
 * frame-object creation) and documents that callbacks entered with a pending
 * exception must preserve it unless they intentionally replace it.
 *
 * Important: this only preserves the raised C-level indicator, not the handled
 * exception state used by sys.exception()/except blocks.
 *
 * Common Python C API functions used with this guard that can set errors:
 * - Frame operations: PyFrame_GetBack() (can set spurious TypeError in Python 3.14+),
 *   PyFrame_GetCode(), PyFrame_GetLineNumber()
 * - Object operations: PyObject_CallObject() (TypeError for bad args, or exceptions from callable),
 *   PyObject_GetAttrString() (AttributeError for missing attributes, or exceptions from getters)
 * - Unicode operations: PyUnicode_AsUTF8AndSize() (TypeError for non-Unicode objects,
 *   or errors during UTF-8 conversion)
 * - Numeric operations: PyLong_AsLongLong() (OverflowError, ValueError, TypeError)
 * - Reference counting: Py_XDECREF()/Py_DECREF() (can trigger exceptions from custom __del__ methods)
 *
 * Example usage:
 *   {
 *       PythonErrorRestorer error_restorer;
 *       // Call Python C API functions that might set errors
 *       // Error state is automatically restored when leaving scope
 *   }
 */
class PythonErrorRestorer
{
  public:
    PythonErrorRestorer()
    {
#ifdef _PY312_AND_LATER
        // Python 3.12+: Use the new API that returns a single exception object
        // Reference ownership note:
        // - PyErr_GetRaisedException() returns a new reference.
        // - PyErr_SetRaisedException() steals a reference.
        // So we intentionally do not DECREF saved_exception ourselves.
        saved_exception = PyErr_GetRaisedException();
#else
        // Python < 3.12: Use the old API with separate type, value, traceback
        PyErr_Fetch(&saved_exc_type, &saved_exc_value, &saved_exc_traceback);
#endif
    }

    ~PythonErrorRestorer()
    {
        // Restore the raised C-level exception indicator that was active on
        // entry, so allocator-hook sampling does not clobber caller state.
        //
        // We still clear transient local failures at call sites where we
        // continue after an API failure (for example, PyUnicode_AsUTF8AndSize
        // and PyFrame_GetBack), because some C-API paths are not safe to keep
        // running with an error set.
#ifdef _PY312_AND_LATER
        if (saved_exception != NULL) {
            PyErr_SetRaisedException(saved_exception);
        } else if (PyErr_Occurred()) {
            PyErr_Clear();
        }
#else
        if (saved_exc_type != NULL || saved_exc_value != NULL || saved_exc_traceback != NULL) {
            PyErr_Restore(saved_exc_type, saved_exc_value, saved_exc_traceback);
        } else if (PyErr_Occurred()) {
            PyErr_Clear();
        }
#endif
    }

    // Non-copyable, non-movable
    PythonErrorRestorer(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer& operator=(const PythonErrorRestorer&) = delete;
    PythonErrorRestorer(PythonErrorRestorer&&) = delete;
    PythonErrorRestorer& operator=(PythonErrorRestorer&&) = delete;

  private:
#ifdef _PY312_AND_LATER
    PyObject* saved_exception;
#else
    PyObject* saved_exc_type;
    PyObject* saved_exc_value;
    PyObject* saved_exc_traceback;
#endif
};
