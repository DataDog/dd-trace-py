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
  : ptype(nullptr)
  , pvalue(nullptr)
  , ptraceback(nullptr)
  , had_exception(false)
{
    py::gil_scoped_acquire acquire;

    PyErr_Fetch(&ptype, &pvalue, &ptraceback);
    had_exception = (ptype != nullptr || pvalue != nullptr || ptraceback != nullptr);
    if (had_exception) {
        PyErr_NormalizeException(&ptype, &pvalue, &ptraceback);
    }
    PyErr_Clear();
}

PythonErrorGuard::~PythonErrorGuard()
{
    restore_or_decref();
}

py::str
PythonErrorGuard::error_as_pystr() const
{
    if (not had_exception) {
        return {};
    }

    const auto pyo = PyObject_Str(pvalue);
    if (pyo == nullptr) {
        return {};
    }
    return py::str(pyo);
}

std::string
PythonErrorGuard::error_as_stdstring() const
{
    if (not had_exception) {
        return {};
    }
    auto pystr = error_as_pystr();
    return error_as_pystr().cast<std::string>();
}

py::str
PythonErrorGuard::traceback_as_pystr() const
{
    if (not had_exception or ptraceback == nullptr) {
        return {};
    }

    auto res = format_traceback(ptraceback, ptype, pvalue);
    return format_traceback(ptraceback, ptype, pvalue);
}

std::string
PythonErrorGuard::traceback_as_stdstring() const
{
    if (not had_exception or ptraceback == nullptr) {
        return {};
    }

    return traceback_as_pystr().cast<std::string>();
}

void
PythonErrorGuard::restore_or_decref()
{
    if (ptype || pvalue || ptraceback) {
        py::gil_scoped_acquire acquire;

        if (had_exception) {
            // Restore the fetched Python error
            PyErr_Restore(ptype, pvalue, ptraceback);
        } else {
            // No exception was present; safely decrement reference counts
            Py_XDECREF(ptype);
            Py_XDECREF(pvalue);
            Py_XDECREF(ptraceback);
        }

        ptype = nullptr;
        pvalue = nullptr;
        ptraceback = nullptr;
    }
}
