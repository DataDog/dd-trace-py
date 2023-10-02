#include "AspectReplace.h"

PyObject*
api_replace_aspect(PyObject* orig_str, PyObject* substr, PyObject* replstr, Py_ssize_t maxcount)
{
    PyObject* result = PyUnicode_Replace(orig_str, substr, replstr, maxcount);

    auto len_result = PyUnicode_GET_LENGTH(result);
    if (len_result == 0) {
        return result;
    }

    auto tx_taint_map = initializer->get_tainting_map();

    const auto& to_orig_str = get_tainted_object(orig_str, tx_taint_map);
    const auto& to_replstr = get_tainted_object(replstr, tx_taint_map);

    if (to_orig_str == nullptr or to_replstr == nullptr) {
        return result;
    }

    auto result_to = initializer->allocate_tainted_object_copy(to_orig_str);
    PyObject* new_result{ new_pyobject_id(result) };
    set_tainted_object(new_result, result_to, tx_taint_map);
    Py_DECREF(result);
    return new_result;
}
