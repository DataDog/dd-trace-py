#pragma once

#include <Python.h>

#include "pymacro.hpp"

/* RAII helper to save and restore Python error state
 *
 * This is useful when calling Python C API functions that may set errors
 * but we want to preserve any existing error state. The error state is
 * automatically restored when the object goes out of scope.
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
        saved_exception = PyErr_GetRaisedException();
#else
        // Python < 3.12: Use the old API with separate type, value, traceback
        PyErr_Fetch(&saved_exc_type, &saved_exc_value, &saved_exc_traceback);
#endif
    }

    ~PythonErrorRestorer()
    {
#ifdef _PY312_AND_LATER
        // Python 3.12+: Restore using the new API
        // Pass nullptr to clear the error indicator if there was no error before
        PyErr_SetRaisedException(saved_exception);
#else
        // Python < 3.12: Restore using the old API
        // If type is nullptr, the error indicator is cleared
        PyErr_Restore(saved_exc_type, saved_exc_value, saved_exc_traceback);
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
