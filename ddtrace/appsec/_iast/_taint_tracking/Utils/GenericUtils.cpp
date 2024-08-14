#include <iostream>

#include "GenericUtils.h"

bool asbool(py::object value) {
    if (value.is_none()) {
        return false;
    }
    if (py::isinstance<py::bool_>(value)) {
        return value.cast<bool>();
    }
    if (py::isinstance<py::str>(value)) {
        std::string str_value = value.cast<std::string>();
        std::transform(str_value.begin(), str_value.end(), str_value.begin(), ::tolower);
        return str_value == "true" || str_value == "1";
    }
    throw std::invalid_argument("Invalid type for asbool function.");
}

bool asbool(const char *value) {
    if (value == nullptr) {
            return false;
    }
    py::object debug_value_py = py::str(value);
    return asbool(debug_value_py);
}

void iast_taint_log_error(const std::string& msg) {
    try {
        if (!is_iast_debug_enabled()) {
            return;
        }

        py::object inspect = py::module::import("inspect");
        py::list stack = inspect.attr("stack")();
        std::string frame_info;

        for (size_t i = 0; i < std::min(stack.size(), static_cast<size_t>(7)); ++i) {
            py::object frame = stack[i];
            py::object frame_info_obj = frame.attr("frame");
            std::string filename = py::str(frame_info_obj.attr("f_code").attr("co_filename"));
            int lineno = py::int_(frame_info_obj.attr("f_lineno"));
            frame_info += filename + ", " + std::to_string(lineno) + "\n";
        }

        auto log = get_python_logger();
        log.attr("debug")(msg + ": " + frame_info);

        py::module metrics = py::module::import("ddtrace.appsec._iast._metrics");
        metrics.attr("_set_iast_error_metric")("IAST propagation error. " + msg);

    } catch (const py::error_already_set& e) {
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
    } catch (const std::exception& e) {
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
    } catch (...) {
        cerr << "ddtrace: unkown error when trying to log an IAST native error";
    }
}

