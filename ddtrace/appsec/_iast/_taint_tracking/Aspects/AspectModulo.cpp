#include "AspectModulo.h"
#include "Helpers.h"

py::object
api_modulo_aspect_pyobject(py::object candidate_text, py::object candidate_tuple)
{
    return candidate_text.attr("__mod__")(candidate_tuple);
}
#include <Python.h>

static PyObject*
do_modulo(PyObject* text, PyObject* insert_tuple_or_obj)
{
    // If the insert argument is not a tuple, wrap it in a tuple
    PyObject* insert_tuple;
    if (!PyTuple_Check(insert_tuple_or_obj)) {
        insert_tuple = PyTuple_Pack(1, insert_tuple_or_obj);
        if (insert_tuple == nullptr) {
            return nullptr; // Return if PyTuple_Pack fails
        }
    } else {
        insert_tuple = insert_tuple_or_obj;
        Py_INCREF(insert_tuple); // Increment the reference count since we'll be using this directly
    }

    PyObject* result = nullptr;

    // Check if text is a Unicode object
    if (PyUnicode_Check(text)) {
        result = PyUnicode_Format(text, insert_tuple);
    }
    // Check if text is a bytes object
    else if (PyBytes_Check(text)) {
#if PY_VERSION_HEX < 0x030C0000 // Python versions earlier than 3.12
        result = PyBytes_Format(text, insert_tuple);
#else // Python 3.12 and later
      // Convert bytes to str, format, and convert back to bytes
        PyObject* text_unicode = PyUnicode_FromEncodedObject(text, "utf-8", "strict");
        if (text_unicode == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

        PyObject* formatted_unicode = PyUnicode_Format(text_unicode, insert_tuple);
        Py_DECREF(text_unicode);
        if (formatted_unicode == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

        result = PyUnicode_AsEncodedString(formatted_unicode, "utf-8", "strict");
        Py_DECREF(formatted_unicode);
#endif
    }
    // Check if text is a bytearray object
    else if (PyByteArray_Check(text)) {
        PyObject* text_bytes = PyBytes_FromStringAndSize(PyByteArray_AsString(text), PyByteArray_Size(text));
        if (text_bytes == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

#if PY_VERSION_HEX < 0x030C0000 // Python versions earlier than 3.12
        PyObject* result_bytes = PyBytes_Format(text_bytes, insert_tuple);
#else // Python 3.12 and later
      // Convert bytes to str, format, and convert back to bytes
        PyObject* text_unicode = PyUnicode_FromEncodedObject(text_bytes, "utf-8", "strict");
        Py_DECREF(text_bytes);
        if (text_unicode == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

        PyObject* formatted_unicode = PyUnicode_Format(text_unicode, insert_tuple);
        Py_DECREF(text_unicode);
        if (formatted_unicode == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

        PyObject* result_bytes = PyUnicode_AsEncodedString(formatted_unicode, "utf-8", "strict");
        Py_DECREF(formatted_unicode);
#endif
        if (result_bytes == nullptr) {
            Py_DECREF(insert_tuple);
            return nullptr;
        }

        result = PyByteArray_FromObject(result_bytes);
        Py_DECREF(result_bytes);
    }
    // If text is not one of the expected types, raise an error
    else {
        return nullptr;
    }

    Py_DECREF(insert_tuple);
    return result;
}

template<class StrType>
StrType
api_modulo_aspect(StrType candidate_text, py::object candidate_tuple)
{
    if (not is_text(candidate_text.ptr())) {
        return candidate_text.attr("__mod__")(candidate_tuple);
    }

    PyObject* pyo_result_o = do_modulo(candidate_text.ptr(), candidate_tuple.ptr());
    if (pyo_result_o == nullptr) {
        // Try with the attr call
        StrType result_o = candidate_text.attr("__mod__")(candidate_tuple);
    }

    StrType result_o = py::reinterpret_borrow<StrType>(pyo_result_o);

    py::tuple parameters =
      py::isinstance<py::tuple>(candidate_tuple) ? candidate_tuple : py::make_tuple(candidate_tuple);

    const auto tx_map = Initializer::get_tainting_map();
    if (!tx_map || tx_map->empty()) {
        return result_o;
    }

    TRY_CATCH_ASPECT("modulo_aspect", , {
        auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text.ptr(), parameters);

        if (ranges_orig.empty()) {
            return result_o;
        }

        // Note: PyCharm could mark an error on this call, but it's only because it doesn't correctly see
        // that at this point even if we entered from the py::object template instantiation, we are guaranteed
        // that the candidate_text is a StrType.
        StrType fmttext = as_formatted_evidence(candidate_text, candidate_text_ranges, TagMappingMode::Mapper);
        py::list list_formatted_parameters;

        for (const py::handle& param_handle : parameters) {
            auto param_strtype = py::reinterpret_borrow<py::object>(param_handle).cast<StrType>();
            if (is_text(param_handle.ptr())) {
                auto [ranges, ranges_error] = get_ranges(param_handle.ptr(), tx_map);
                StrType n_parameter = as_formatted_evidence(param_strtype, ranges, TagMappingMode::Mapper, nullopt);
                list_formatted_parameters.append(n_parameter);
            } else {
                list_formatted_parameters.append(param_handle);
            }
        }
        py::tuple formatted_parameters(list_formatted_parameters);

        PyObject* pyo_applied_params = do_modulo(fmttext.ptr(), formatted_parameters.ptr());

        StrType applied_params;
        if (pyo_applied_params == nullptr) {
            applied_params = fmttext.attr("__mod__")(formatted_parameters);
        } else {
            applied_params = py::reinterpret_borrow<StrType>(pyo_applied_params);
        }

        return api_convert_escaped_text_to_taint_text(applied_params, ranges_orig);
    });
}

void
pyexport_aspect_modulo(py::module& m)
{
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::str>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::bytes>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    // JJJ
    // m.def("_aspect_modulo",
    //       &api_modulo_aspect<py::bytearray>,
    //       "candidate_text"_a,
    //       "candidate_tuple"_a,
    //       py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect_pyobject,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
}
