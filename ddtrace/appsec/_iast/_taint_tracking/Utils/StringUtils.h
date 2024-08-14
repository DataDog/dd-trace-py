#pragma once

#include <Python.h>
#include <pybind11/pybind11.h>
#include <iostream> // JJJ

using namespace std;
using namespace pybind11::literals;

namespace py = pybind11;

inline static uintptr_t
get_unique_id(const PyObject* str)
{
    return reinterpret_cast<uintptr_t>(str);
}

bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr);

void
set_fast_tainted_if_notinterned_unicode(PyObject* objptr);

inline bool
is_text(const PyObject* pyptr)
{
    if (!pyptr)
        return false;

    return PyUnicode_Check(pyptr) || PyBytes_Check(pyptr) || PyByteArray_Check(pyptr);
}

// JJJ move to GenericUtils.cpp
inline bool asbool(py::object value) {
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

// JJJ move to GenericUtils.cpp
inline bool asbool(const char *value) {
    if (value == nullptr) {
            return false;
    }
    py::object debug_value_py = py::str(value);
    return asbool(debug_value_py);
}


// JJJ move to GenericUtils.cpp
inline bool is_iast_debug_enabled() {
    const char* debug_value = std::getenv("IAST_DEBUG");
    if (debug_value == nullptr) {
        return false;
    }
    return asbool(debug_value);
}

// JJJ move to GenericUtils.cpp
inline py::object get_python_logger() {
    py::object logger_module = py::module::import("ddtrace.internal.logger");
    py::object get_logger = logger_module.attr("get_logger");
    py::object native_logger = get_logger("native");
    return native_logger;
}

// JJJ move to GenericUtils.cpp
inline void iast_taint_log_error(const std::string& msg) {
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


// Base function for the variadic template
inline
bool all_are_text_and_same_type(PyObject* first) {
    return (first != nullptr) and is_text(first);
}

// Recursive case for the argument checking variadic template
template<typename... Args>
inline bool all_are_text_and_same_type(PyObject* first, PyObject* second, Args... args) {
    // Check if both first and second are valid text types and of the same type
    if (first == nullptr || second == nullptr || !is_text(first) || !is_text(second) || PyObject_Type(first) != PyObject_Type(second)) {
        return false;
    }

    // Recursively check the rest of the arguments
    return all_are_text_and_same_type(second, args...);
}

string
PyObjectToString(PyObject* obj);

PyObject*
new_pyobject_id(PyObject* tainted_object);

size_t
get_pyobject_size(PyObject* obj);