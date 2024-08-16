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
    cerr << "JJJ taint_log_1===================\n";
    try {
        if (!is_iast_debug_enabled()) {
            cerr << "JJJ taint_log_2\n";
            return;
        }

        // Try to get the file and line number of the caller
        std::string frame_info;
        try {
            cerr << "JJJ taint_log_1.1\n";
            const py::module inspect = py::module::import("inspect");
            cerr << "JJJ taint_log_1.2\n";
            const py::list stack = inspect.attr("stack")();
            cerr << "JJJ taint_log_1.3\n";
            cerr << "JJJ taint_log_1.4\n";

            cerr << "JJJ taint_log_3\n";
            for (size_t i = 0; i < std::min(stack.size(), static_cast<size_t>(7)); ++i) {
                py::object frame = stack[i];
                py::object frame_info_obj = frame.attr("frame");
                std::string filename = py::str(frame_info_obj.attr("f_code").attr("co_filename"));
                const int lineno = py::int_(frame_info_obj.attr("f_lineno"));
                frame_info += filename + ", " + std::to_string(lineno) + "\n";
                cerr << "JJJ taint_log_4\n";
            }
        } catch (const py::error_already_set& e) {
            cerr << "JJJ taint_log_5\n";
            cerr << "ddtrace: error in iast_taint_log_error trying to retrieve file and line: " << e.what() << "\n";
            PyErr_Clear(); // Clear the error state
            frame_info = "(unkown file)";
        }

        const auto log = get_python_logger();
        log.attr("debug")(msg + ": " + frame_info);

        const py::module metrics = py::module::import("ddtrace.appsec._iast._metrics");
        metrics.attr("_set_iast_error_metric")("IAST propagation error. " + msg);
        cerr << "JJJ taint_log_5\n";

    } catch (const py::error_already_set& e) {
        cerr << "JJJ iast_taint_log_error exception 1\n";
        if (!e.trace().is_none()) {
            cerr << "JJJ exc: 1, what: " << e.what() << "\n";
            cerr << "JJJ exc: 2\n";

            PyObject *type, *value, *tb;
            PyErr_Fetch(&type, &value, &tb);
            if (value) {
                std::cerr << "Exception value: " << py::str(value).cast<std::string>() << "\n";
            }
            if (tb) {
                std::cerr << "Traceback:\n" << py::str(tb).cast<std::string>() << "\n";
            }
        }
        cerr << "JJJ exc: 6\n";
        // std::cerr << "Traceback: " << e.trace().cast<std::string>() << "\n";      // Print the traceback
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
        PyErr_Clear(); // Clear the error state
    } catch (const std::exception& e) {
        cerr << "JJJ iast_taint_log_error exception 2\n";
        cerr << "ddtrace: error when trying to log an IAST native error: " << e.what() << "\n";
    } catch (...) {
        cerr << "JJJ iast_taint_log_error exception 3\n";
        cerr << "ddtrace: unkown error when trying to log an IAST native error";
    }
}
