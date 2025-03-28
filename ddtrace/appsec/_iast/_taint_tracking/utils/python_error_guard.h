#pragma once

#include <pybind11/pybind11.h>

namespace py = pybind11;

/**
 * @brief RAII class to manage Python error states.
 *
 * This class fetches any existing Python error upon construction,
 * clears the error state to allow safe C++ operations, and
 * restores the error (if any) upon destruction.
 * WARNING: If you alter the error state in any way, like caling
 * PyErr_Clear while the guard is active, undefined behaviour will occur
 * since internally it will not be updated!
 */
class PythonErrorGuard
{
  public:
    /**
     * @brief Constructs the PythonErrorGuard.
     *
     * Fetches any existing Python error, stores it, and clears the error state.
     */
    PythonErrorGuard();

    /**
     * @brief Destructor.
     *
     * Restores the fetched Python error if one was present, or decrements
     * reference counts of fetched error objects if no error was present.
     */
    ~PythonErrorGuard();

    // Delete copy constructor and copy assignment operator
    PythonErrorGuard(const PythonErrorGuard&) = delete;
    PythonErrorGuard& operator=(const PythonErrorGuard&) = delete;

    // Same for move constructor and move assignment operator
    PythonErrorGuard(PythonErrorGuard&& other) noexcept = delete;
    PythonErrorGuard& operator=(PythonErrorGuard&& other) noexcept = delete;
    [[nodiscard]] bool has_error() const { return had_exception; }
    [[nodiscard]] py::str error_as_pystr() const;
    [[nodiscard]] std::string error_as_stdstring() const;
    [[nodiscard]] py::str traceback_as_pystr() const;
    [[nodiscard]] std::string traceback_as_stdstring() const;

  private:
    PyObject* ptype;
    PyObject* pvalue;
    PyObject* ptraceback;
    bool had_exception;

    void restore_or_decref();
};