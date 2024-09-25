#include <iostream>

#include "GenericUtils.h"

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
    const py::module metrics = py::module::import("ddtrace.appsec._iast._metrics");
    metrics.attr("_set_iast_error_metric")("[IAST] Propagation error. " + msg);
    try {
        if (!is_iast_debug_enabled()) {
            return;
        }
        std::string frame_info;
        try {
            const py::module inspect = py::module::import("inspect");
            const py::list stack = inspect.attr("stack")();

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
        log.attr("debug")("[IAST] Propagation error. " + msg + ": " + frame_info);
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
            Py_XDECREF(type);
            Py_XDECREF(value);
            Py_XDECREF(tb);
        }
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
        PyErr_Clear(); // Clear the error state
    } catch (const std::exception& e) {
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
    } catch (...) {
        cerr << "ddtrace: unkown error when trying to log an IAST native error";
    }
}
