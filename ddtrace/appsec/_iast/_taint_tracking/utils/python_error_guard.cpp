#include "python_error_guard.h"
#include <pybind11/pybind11.h>

static py::str
format_traceback(PyObject* ptraceback, PyObject* exc_type, PyObject* exc_value)
{
    if (ptraceback == nullptr) {
        return { "" };
    }

    try {
        const auto traceback_module = py::module_::import("traceback");
        auto formatted_tb = traceback_module.attr("format_tb")(py::handle(ptraceback));
        const auto tb_str = py::str("").attr("join")(formatted_tb);
        py::object formatted_exc =
          traceback_module.attr("format_exception_only")(py::handle(exc_type), py::handle(exc_value));
        const auto exc_str = py::str("").attr("join")(formatted_exc);
        std::string complete_traceback =
          "Traceback (most recent call last):\n" + tb_str.cast<std::string>() + exc_str.cast<std::string>();
        return { complete_traceback };
    } catch (const py::error_already_set& e) {
        return { (std::string("Failed to format traceback: ") + e.what()).c_str() };
    }
}

PythonErrorGuard::PythonErrorGuard()
  : had_exception(false)
{
    py::gil_scoped_acquire acquire;

    PyObject* raw_type = nullptr;
    PyObject* raw_value = nullptr;
    PyObject* raw_traceback = nullptr;
    PyErr_Fetch(&raw_type, &raw_value, &raw_traceback);
    had_exception = (raw_type != nullptr || raw_value != nullptr || raw_traceback != nullptr);
    if (had_exception) {
        PyErr_NormalizeException(&raw_type, &raw_value, &raw_traceback);
    }
    ptype = py::reinterpret_steal<py::object>(raw_type);
    pvalue = py::reinterpret_steal<py::object>(raw_value);
    ptraceback = py::reinterpret_steal<py::object>(raw_traceback);
    PyErr_Clear();
}

PythonErrorGuard::~PythonErrorGuard() noexcept
{
    restore();
}

py::str
PythonErrorGuard::error_as_pystr() const
{
    if (not had_exception) {
        return {};
    }

    PyObject* pyo = PyObject_Str(pvalue.ptr());
    if (pyo == nullptr) {
        return {};
    }
    return py::reinterpret_steal<py::str>(pyo);
}

std::string
PythonErrorGuard::error_as_stdstring() const
{
    if (not had_exception) {
        return {};
    }
    return error_as_pystr().cast<std::string>();
}

py::str
PythonErrorGuard::traceback_as_pystr() const
{
    if (not had_exception or !ptraceback) {
        return {};
    }

    return format_traceback(ptraceback.ptr(), ptype.ptr(), pvalue.ptr());
}

std::string
PythonErrorGuard::traceback_as_stdstring() const
{
    if (not had_exception or !ptraceback) {
        return {};
    }

    return traceback_as_pystr().cast<std::string>();
}

void
PythonErrorGuard::restore() noexcept
{
    if (ptype || pvalue || ptraceback) {
        // Keep destructor cleanup on CPython's non-throwing GIL API.
        const PyGILState_STATE gil_state = PyGILState_Ensure();

        if (had_exception) {
            // Restore the fetched Python error
            PyErr_Restore(ptype.release().ptr(), pvalue.release().ptr(), ptraceback.release().ptr());
        } else {
            ptype = {};
            pvalue = {};
            ptraceback = {};
        }
        PyGILState_Release(gil_state);
    }
}
