#pragma once

#include <Python.h>

/* RAII helper to save and restore Python error state
 *
 * This is useful when calling Python C API functions that may set errors
 * but we want to preserve any existing error state. The error state is
 * automatically restored when the object goes out of scope.
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
        // Pass NULL to clear the error indicator if there was no error before
        PyErr_SetRaisedException(saved_exception);
#else
        // Python < 3.12: Restore using the old API
        // If type is NULL, the error indicator is cleared
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
