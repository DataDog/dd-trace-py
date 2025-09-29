#include "aspect_modulo.h"
#include "helpers.h"

static PyObject*
do_modulo(PyObject* text, PyObject* insert_tuple_or_obj)
{
    // Early return: if left operand is not text-like, preserve native % behavior.
    // Do NOT coerce the right operand to a tuple; arithmetic expects the raw operand.

    // Normalize parameters:
    // - If mapping or tuple: use as-is (borrowed reference, do not INCREF/DECREF)
    // - Else: pack single value into a new 1-tuple we own and must DECREF
    PyObject* params = insert_tuple_or_obj; // borrowed
    bool own_params = false;
    if (!PyTuple_Check(insert_tuple_or_obj) && !PyMapping_Check(insert_tuple_or_obj)) {
        params = PyTuple_Pack(1, insert_tuple_or_obj); // new ref
        if (params == nullptr) {
            return nullptr;
        }
        own_params = true;
    }

    PyObject* result = nullptr;
    if (PyUnicode_Check(text)) {
        result = PyUnicode_Format(text, params);
    } else if (PyBytes_Check(text) || PyByteArray_Check(text)) {
        // Use generic numeric remainder which maps to % operator without creating a method name
        result = PyNumber_Remainder(text, params);
    } else {
        // Fallback: try generic % operator; if unsupported, Python will raise
        result = PyNumber_Remainder(text, params);
    }

    if (own_params) {
        Py_DECREF(params);
    }
    // result is a new reference from PyUnicode_Format/PyNumber_Remainder; return as-is
    return result;
}

PyObject*
api_modulo_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 3) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* candidate_tuple = args[1];
    PyObject* candidate_result = args[2]; // borrowed from caller

    // Helper: return the precomputed result safely (INCREF because it's a borrowed ref)
    auto return_candidate_result = [&]() -> PyObject* {
        if (candidate_result == nullptr) {
            // propagate error if any
            return nullptr;
        }
        Py_INCREF(candidate_result);
        return candidate_result;
    };

    const auto tx_map =
      taint_engine_context->get_tainted_object_map_from_list_of_pyobjects({ candidate_text, candidate_tuple });
    if (!tx_map || tx_map->empty()) {
        return return_candidate_result();
    }

    const auto py_candidate_text = py::reinterpret_borrow<py::object>(candidate_text);
    auto py_candidate_tuple = py::reinterpret_borrow<py::object>(candidate_tuple);

    const auto py_str_type = get_pytext_type(candidate_text);
    if (py_str_type == PyTextType::OTHER) {
        // Not a text formatting case; use the already computed result
        return return_candidate_result();
    }

    const py::tuple parameters =
      py::isinstance<py::tuple>(py_candidate_tuple) ? py_candidate_tuple : py::make_tuple(py_candidate_tuple);

    auto [ranges_orig, candidate_text_ranges] = are_all_text_all_ranges(candidate_text, parameters, tx_map);

    if (ranges_orig.empty()) {
        return return_candidate_result();
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
        return return_candidate_result();
    }

    auto res_pyobject = api_convert_escaped_text_to_taint_text(applied_params, ranges_orig, py_str_type);
    Py_DECREF(applied_params);
    if (res_pyobject == nullptr) {
        return return_candidate_result();
    }
    return res_pyobject;
}
