#include "AspectModulo.h"
#include "Helpers.h"
#include <iostream> // JJJ

py::object
api_modulo_aspect_pyobject(const py::object& candidate_text, const py::object& candidate_tuple)
{
    return candidate_text.attr("__mod__")(candidate_tuple);
}

static PyObject*
do_modulo(PyObject* text, PyObject* insert_tuple_or_obj)
{
    PyObject* result = nullptr;

    // If the second argument is not a tuple and not a mapping, wrap it in a tuple
    PyObject* insert_tuple = nullptr;
    if (PyMapping_Check(insert_tuple_or_obj) or PyTuple_Check(insert_tuple_or_obj)) {
        insert_tuple = insert_tuple_or_obj;
        Py_INCREF(insert_tuple); // Increment the reference count since we'll be using this directly
    } else {
        insert_tuple = PyTuple_Pack(1, insert_tuple_or_obj);
        if (insert_tuple == nullptr) {
            return nullptr; // Return if PyTuple_Pack fails
        }
    }

    // Check if text is a Unicode object
    if (PyUnicode_Check(text)) {
        result = PyUnicode_Format(text, insert_tuple);
    }
    // Check if text is a bytes object
    else if (PyBytes_Check(text)) {
        // Convert bytes to str, format, and convert back to bytes
        if (PyObject* text_unicode = PyUnicode_FromEncodedObject(text, "utf-8", "strict"); text_unicode != nullptr) {
            result = PyUnicode_Format(text_unicode, insert_tuple);
            Py_DECREF(text_unicode);

            if (result != nullptr) {
                PyObject* encoded_result = PyUnicode_AsEncodedString(result, "utf-8", "strict");
                Py_DECREF(result);
                result = encoded_result;
            }
        }
    }
    // Check if text is a bytearray object
    else if (PyByteArray_Check(text)) {
        if (PyObject* text_bytes = PyBytes_FromStringAndSize(PyByteArray_AsString(text), PyByteArray_Size(text));
            text_bytes != nullptr) {
            PyObject* text_unicode = PyUnicode_FromEncodedObject(text_bytes, "utf-8", "strict");
            Py_DECREF(text_bytes);
            if (text_unicode != nullptr) {
                result = PyUnicode_Format(text_unicode, insert_tuple);
                Py_DECREF(text_unicode);

                if (result != nullptr) {
                    PyObject* encoded_result = PyUnicode_AsEncodedString(result, "utf-8", "strict");
                    Py_DECREF(result);
                    result = encoded_result;
                }
            }
        }

        if (result != nullptr) {
            PyObject* result_bytearray = PyByteArray_FromObject(result);
            Py_DECREF(result);
            result = result_bytearray;
        }
    }
    // If text is not one of the expected types, raise an error
    else {
        Py_DECREF(insert_tuple);
        return nullptr;
    }

    Py_DECREF(insert_tuple);
    return result;
}

PyObject*
api_modulo_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    cerr << "JJJ 1\n";
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        cerr << "JJJ 2\n";
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* candidate_tuple = args[1];

    const auto py_candidate_text = py::reinterpret_borrow<py::object>(candidate_text);
    auto py_candidate_tuple = py::reinterpret_borrow<py::object>(candidate_tuple);

    const auto py_str_type = get_pytext_type(args[0]);
    cerr << "JJJ 3\n";
    if (py_str_type == PyTextType::OTHER) {
        cerr << "JJJ 4\n";
        return py_candidate_text.attr("__mod__")(py_candidate_tuple).ptr();
    }

    const py::tuple parameters =
      py::isinstance<py::tuple>(py_candidate_tuple) ? py_candidate_tuple : py::make_tuple(py_candidate_tuple);

    // Lambda to get the result of the modulo operation
    auto get_result = [&]() -> PyObject* {
        PyObject* res = do_modulo(candidate_text, candidate_tuple);
        if (res == nullptr) {
            return py_candidate_text.attr("__mod__")(py_candidate_tuple).ptr();
        }
        return res;
    };

    const auto tx_map = Initializer::get_tainting_map();
    cerr << "JJJ 5\n";
    if (!tx_map || tx_map->empty()) {
        cerr << "JJJ 6\n";
        return get_result();
    }

    TRY_CATCH_ASPECT("modulo_aspect", return get_result(), , {
        cerr << "JJJ 7\n";
        auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text, parameters);

        if (ranges_orig.empty()) {
            cerr << "JJJ 8\n";
            return get_result();
        }

        auto std_candidate_text = py_candidate_text.cast<string>();
        auto fmttext = as_formatted_evidence(std_candidate_text, candidate_text_ranges, TagMappingMode::Mapper);
        py::list list_formatted_parameters;

        for (const py::handle& param_handle : parameters) {
            if (is_text(param_handle.ptr())) {
                auto [ranges, ranges_error] = get_ranges(param_handle.ptr(), tx_map);
                string n_parameter =
                  as_formatted_evidence(AnyTextPyObjectToString(param_handle), ranges, TagMappingMode::Mapper, nullopt);
                list_formatted_parameters.append(StringToPyObject(n_parameter, py_str_type));
            } else {
                list_formatted_parameters.append(param_handle);
            }
        }
        py::tuple formatted_parameters(list_formatted_parameters);

        PyObject* applied_params = do_modulo(StringToPyObject(fmttext, py_str_type).ptr(), formatted_parameters.ptr());
        cerr << "JJJ 9\n";
        if (applied_params == nullptr) {
            cerr << "JJJ 10\n";
            return get_result();
        }

        cerr << "JJJ return, applied_params: " << py::str(applied_params) << "\n";
        auto res = convert_escaped_text_to_taint_text(applied_params, ranges_orig);
        Py_DECREF(applied_params);
        if (res == nullptr) {
            cerr << "JJJ nullptr result from convert\n";
            return get_result();
        }
        cerr << "JJJ final return, value: " << py::str(res) << endl;
        return res;
    });
}
