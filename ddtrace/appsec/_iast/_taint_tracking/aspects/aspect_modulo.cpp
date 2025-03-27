#include "aspect_modulo.h"
#include "helpers.h"

static PyObject*
do_modulo(PyObject* text, PyObject* insert_tuple_or_obj)
{
    PyObject* result = nullptr;

    // If the second argument is not a tuple and not a mapping, wrap it in a tuple
    PyObject* insert_tuple = nullptr;
    if (PyMapping_Check(insert_tuple_or_obj) or PyTuple_Check(insert_tuple_or_obj)) {
        insert_tuple = insert_tuple_or_obj;
        Py_INCREF(insert_tuple);
    } else {
        insert_tuple = PyTuple_Pack(1, insert_tuple_or_obj);
        if (insert_tuple == nullptr) {
            return nullptr;
        }
    }

    if (PyUnicode_Check(text)) {
        result = PyUnicode_Format(text, insert_tuple);
    } else if (PyBytes_Check(text) or PyByteArray_Check(text)) {
        auto method_name = PyUnicode_FromString("__mod__");
        result = PyObject_CallMethodObjArgs(text, method_name, insert_tuple, nullptr);
        Py_DECREF(method_name);
    } else {
    }
    Py_DECREF(insert_tuple);
    if (has_pyerr()) {
        Py_XDECREF(result);
        return nullptr;
    }
    return result;
}

PyObject*
api_modulo_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* candidate_tuple = args[1];

    const auto py_candidate_text = py::reinterpret_borrow<py::object>(candidate_text);
    auto py_candidate_tuple = py::reinterpret_borrow<py::object>(candidate_tuple);

    // Lambda to get the result of the modulo operation
    auto get_result = [&]() -> PyObject* {
        PyObject* res = do_modulo(candidate_text, candidate_tuple);
        if (res == nullptr) {
            try {
                py::object res_py = py_candidate_text.attr("__mod__")(py_candidate_tuple);
                PyObject* res_pyo = res_py.ptr();
                if (res_pyo != nullptr) {
                    Py_INCREF(res_pyo);
                }
                return res_pyo;
            } catch (py::error_already_set& e) {
                e.restore();
                return nullptr;
            }
        }
        return res;
    };

    TRY_CATCH_ASPECT("modulo_aspect", return get_result(), , {
        const auto py_str_type = get_pytext_type(args[0]);
        if (py_str_type == PyTextType::OTHER) {
            try {
                return get_result();
            } catch (py::error_already_set& e) {
                e.restore();
                return nullptr;
            }
        }

        const py::tuple parameters =
          py::isinstance<py::tuple>(py_candidate_tuple) ? py_candidate_tuple : py::make_tuple(py_candidate_tuple);

        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
            return get_result();
        }

        auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text, parameters);

        if (ranges_orig.empty()) {
            return get_result();
        }

        auto std_candidate_text = py_candidate_text.cast<string>();
        auto fmttext = as_formatted_evidence(std_candidate_text, candidate_text_ranges, TagMappingMode::Mapper);
        py::list list_formatted_parameters;

        for (const py::handle& param_handle : parameters) {
            if (is_text(param_handle.ptr())) {
                auto [ranges, ranges_error] = get_ranges(param_handle.ptr(), tx_map);
                string n_parameter =
                  as_formatted_evidence(AnyTextObjectToString(param_handle), ranges, TagMappingMode::Mapper, nullopt);
                list_formatted_parameters.append(StringToPyObject(n_parameter, py_str_type));
            } else {
                list_formatted_parameters.append(param_handle);
            }
        }
        py::tuple formatted_parameters(list_formatted_parameters);

        PyObject* applied_params = do_modulo(StringToPyObject(fmttext, py_str_type).ptr(), formatted_parameters.ptr());
        if (applied_params == nullptr) {
            return get_result();
        }

        auto res_pyobject = api_convert_escaped_text_to_taint_text(applied_params, ranges_orig, py_str_type);
        Py_DECREF(applied_params);
        if (res_pyobject == nullptr) {
            return get_result();
        }
        return res_pyobject;
    });
}
