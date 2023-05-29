#include "AspectOperatorAdd.h"

PyObject* add_aspect(PyObject* result_o, PyObject* candidate_text, PyObject* text_to_add, TaintRangeMapType* tx_taint_map) {
    size_t len_candidate_text, len_text_to_add, len_result_o;
    if (PyUnicode_Check(candidate_text)) {
        len_candidate_text = PyUnicode_GET_LENGTH(candidate_text);
        len_text_to_add = PyUnicode_GET_LENGTH(text_to_add);
        len_result_o = PyUnicode_GET_LENGTH(result_o);
    } else if (PyBytes_Check(candidate_text)){
        len_candidate_text = PyByteArray_Size(candidate_text);
        len_text_to_add = PyBytes_Size(text_to_add);
        len_result_o = PyBytes_Size(result_o);
    } else if (PyByteArray_Check(candidate_text)){
        len_candidate_text = PyByteArray_Size(candidate_text);
        len_text_to_add = PyByteArray_Size(text_to_add);
        len_result_o = PyByteArray_Size(result_o);
    }

    if (len_text_to_add == 0 and len_candidate_text > 0) {
        return candidate_text;
    }
    if (len_text_to_add > 0 and len_candidate_text == 0 and text_to_add == result_o) {
        return text_to_add;
    }

    const auto& to_initial = get_tainted_object(candidate_text, tx_taint_map);
    if (to_initial and to_initial->get_ranges().size() >= TaintedObject::TAINT_RANGE_LIMIT) {
        const auto& res_new_id = new_pyobject_id(result_o, len_result_o);
        // If left side is already at the maximum taint ranges, we just reuse its ranges,
        // we don't need to look at left side.
        set_tainted_object(res_new_id, to_initial, tx_taint_map);
        return res_new_id;
    }

    const auto& to_to_add = get_tainted_object(text_to_add, tx_taint_map);
    if (!to_initial and !to_to_add) {
        return result_o;
    }
    if (!to_to_add) {
        const auto& res_new_id = new_pyobject_id(result_o, len_result_o);
        set_tainted_object(res_new_id, to_initial, tx_taint_map);
        return res_new_id;
    }

    auto to = initializer->allocate_tainted_object(to_initial);
    to->add_ranges_shifted(to_to_add, (long) len_candidate_text);

    const auto& res_new_id = new_pyobject_id(result_o, len_result_o);
    set_tainted_object(res_new_id, to, tx_taint_map);

    return res_new_id;
}

PyObject* api_add_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    if (nargs != 2) {
        // TODO: any other more sane error handling?
        return nullptr;
    }
    PyObject* candidate_text = args[0];
    PyObject* text_to_add = args[1];

    PyObject *result_o;
    if (PyUnicode_Check(candidate_text)) {
        result_o = PyUnicode_Concat(candidate_text, text_to_add);
    } else if (PyBytes_Check(candidate_text)){
        PyBytes_Concat(&candidate_text, text_to_add);
        result_o = candidate_text;
        candidate_text = tmp_bytes;
    } else if (PyByteArray_Check(candidate_text)){
        result_o = PyByteArray_Concat(candidate_text, text_to_add);
    }

    if (!could_be_tainted(candidate_text) && !could_be_tainted(text_to_add)) {
        return result_o;
    }


    auto ctx_map = initializer->get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
        return result_o;
    }

    return add_aspect(result_o, candidate_text, text_to_add, ctx_map);
}