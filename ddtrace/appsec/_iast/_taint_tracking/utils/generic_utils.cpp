#include <iostream>
#include <pybind11/pybind11.h>

#include "generic_utils.h"

#include "aspects/helpers.h"
#include "initializer/initializer.h"
#include "python_error_guard.h"

bool
asbool(const py::object& value)
{
    if (value.is_none()) {
        return false;
    }
    if (py::isinstance<py::bool_>(value)) {
        return value.cast<bool>();
    }
    if (py::isinstance<py::str>(value)) {
        auto str_value = value.cast<std::string>();
        std::transform(str_value.begin(), str_value.end(), str_value.begin(), ::tolower);
        return str_value == "true" || str_value == "1";
    }
    throw std::invalid_argument("Invalid type for asbool function.");
}

bool
asbool(const char* value)
{
    if (value == nullptr) {
        return false;
    }
    const py::object debug_value_py = py::str(value);
    return asbool(debug_value_py);
}

void
iast_taint_log_error(const std::string& msg)
{
    safe_import("ddtrace.appsec._iast._metrics", "_set_iast_error_metric")("iast::propagation::native::error::" + msg);
    try {
        if (!is_iast_debug_enabled()) {
            return;
        }
        std::string frame_info;
        // If we don't clear the error, stack() and other functions won't work, so we save it first for restoring
        // it later if needed
        PythonErrorGuard error_guard;

        try {
            const py::list stack = safe_import("inspect", "stack")();

            for (size_t i = 0; i < std::min(stack.size(), static_cast<size_t>(7)); ++i) {
                py::object frame = stack[i];
                py::object frame_info_obj = frame.attr("frame");
                std::string filename = py::str(frame_info_obj.attr("f_code").attr("co_filename"));
                const int lineno = py::int_(frame_info_obj.attr("f_lineno"));
                frame_info += filename + ", " + std::to_string(lineno) + "\n";
            }
        } catch (const py::error_already_set& e) {
            cerr << "ddtrace: error in iast_taint_log_error trying to retrieve file and line: " << e.what() << "\n";
            PyErr_Clear(); // Clear the error state
            frame_info = "(unkown file)";
        }

        const auto log = get_python_logger();
        log.attr("debug")("iast::propagation::native::error::" + msg + ": " + frame_info);

    } catch (const py::error_already_set& e) {
        if (!e.trace().is_none()) {
            if (PythonErrorGuard error_guard; error_guard.has_error()) {
                std::cerr << "Traceback: " << error_guard.error_as_stdstring() << "\n";
            }
        }
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
        PyErr_Clear();
    } catch (const std::exception& e) {
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
    } catch (...) {
        cerr << "ddtrace: unkown error when trying to log an IAST native error";
    }
}

inline py::object
get_python_logger()
{
    return safe_import("ddtrace.internal.logger", "get_logger")("native");
}

py::object
safe_import(const char* module_name, const char* symbol_name)
{
    if (module_name == nullptr) {
        return py::none();
    }

    const string final_name =
      symbol_name == nullptr ? string(module_name) : string(module_name) + "." + string(symbol_name);

    PythonErrorGuard error_guard;

    py::object ret;
    const auto mod = py::module_::import(module_name);

    if (symbol_name != nullptr) {
        auto attr = mod.attr(symbol_name);
        ret = attr;
    } else {
        ret = mod;
    }

    return ret;
}

bool
is_pointer_this_builtin(PyObject* orig_function, const char* builtin_name)
{
    if (!orig_function) {
        return false;
    }

    static PyObject* builtin = nullptr;
    if (builtin == nullptr) {
        PyObject* builtins = PyImport_ImportModule("builtins");
        if (!builtins) {
            return false;
        }

        builtin = PyObject_GetAttrString(builtins, builtin_name);
        Py_DECREF(builtins);

        if (!builtin) {
            return false;
        }
    }

    return orig_function == builtin;
}
