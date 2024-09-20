#include <iostream>

#include "GenericUtils.h"

#include "Aspects/Helpers.h"
#include "Initializer/Initializer.h"

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
    try {
        if (!is_iast_debug_enabled()) {
            return;
        }
        std::string frame_info;
        // If we don't clear the error, stack() and other functions won't work, so we save it first for restoring
        // it later if needed
        PyObject *ptype, *pvalue, *ptraceback;
        PyErr_Fetch(&ptype, &pvalue, &ptraceback);
        const bool had_exception = (ptype != nullptr || pvalue != nullptr || ptraceback != nullptr);
        PyErr_Clear();

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
        log.attr("debug")(msg + ": " + frame_info);

        safe_import("ddtrace.appsec._iast._metrics", "_set_iast_error_metric")("IAST propagation error. " + msg);

        // Restore the original exception state if needed
        if (had_exception) {
            PyErr_Restore(ptype, pvalue, ptraceback);
        } else {
            Py_XDECREF(ptype);
            Py_XDECREF(pvalue);
            Py_XDECREF(ptraceback);
        }

    } catch (const py::error_already_set& e) {
        if (!e.trace().is_none()) {

            PyObject *type, *value, *tb;
            PyErr_Fetch(&type, &value, &tb);
            PyErr_NormalizeException(&type, &value, &tb);
            if (value) {
                std::cerr << "Exception value: " << py::str(value).cast<std::string>() << "\n";
            }
            if (tb) {
                std::cerr << "Traceback:\n" << py::str(tb).cast<std::string>() << "\n";
            }
        }
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
        PyErr_Clear(); // Clear the error state
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

    // First check if there is any error and store it to restore later and clear it because
    // otherwise we can't import anything
    PyObject *ptype, *pvalue, *ptraceback;
    PyErr_Fetch(&ptype, &pvalue, &ptraceback); // Fetch and clear the current exception
    const bool had_exception = (ptype != nullptr || pvalue != nullptr || ptraceback != nullptr);

    py::object ret;
    const auto mod = py::module_::import(module_name);

    if (symbol_name != nullptr) {
        auto attr = mod.attr(symbol_name);
        ret = attr;
    } else {
        ret = mod;
    }

    if (had_exception) {
        PyErr_Restore(ptype, pvalue, ptraceback);
    } else {
        Py_XDECREF(ptype);
        Py_XDECREF(pvalue);
        Py_XDECREF(ptraceback);
    }

    return ret;
}
