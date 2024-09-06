#include "AspectModulo.h"
#include "Helpers.h"

py::object
api_modulo_aspect_pyobject(py::object candidate_text, py::object candidate_tuple)
{
    return candidate_text.attr("__mod__")(candidate_tuple);
}

static PyObject*
do_modulo(PyObject* text, PyObject* insert_tuple_or_obj)
{
    PyObject* result = nullptr;

    // If the second argument is not a tuple and not a mapping, wrap it in a tuple
    PyObject* insert_tuple = nullptr;
    if (PyMapping_Check(insert_tuple_or_obj)) {
        insert_tuple = insert_tuple_or_obj;
        Py_INCREF(insert_tuple); // Increment the reference count since we'll be using this directly
    } else if (PyTuple_Check(insert_tuple_or_obj)) {
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
        PyObject* text_unicode = PyUnicode_FromEncodedObject(text, "utf-8", "strict");
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
    // Check if text is a bytearray object
    else if (PyByteArray_Check(text)) {
        PyObject* text_bytes = PyBytes_FromStringAndSize(PyByteArray_AsString(text), PyByteArray_Size(text));
        if (text_bytes != nullptr) {
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

template<class StrType>
StrType
api_modulo_aspect(StrType candidate_text, py::object candidate_tuple)
{
    if (not is_text(candidate_text.ptr())) {
        return candidate_text.attr("__mod__")(candidate_tuple);
    }

    PyObject* pyo_result_o = do_modulo(candidate_text.ptr(), candidate_tuple.ptr());
    if (pyo_result_o == nullptr) {
        return candidate_text.attr("__mod__")(candidate_tuple);
    }

    StrType result_o = py::reinterpret_steal<StrType>(pyo_result_o);
    const py::tuple parameters =
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
            return result_o;
        } else {
            applied_params = py::reinterpret_steal<StrType>(pyo_applied_params);
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
    m.def("_aspect_modulo",
          &api_modulo_aspect<py::bytearray>,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
    m.def("_aspect_modulo",
          &api_modulo_aspect_pyobject,
          "candidate_text"_a,
          "candidate_tuple"_a,
          py::return_value_policy::move);
}
