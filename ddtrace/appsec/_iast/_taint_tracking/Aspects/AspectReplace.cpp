#include "AspectReplace.h"
#define PyBUF_SIMPLE 0

PyObject*
api_replace_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    PyObject* orig_result = args[0];
    Py_ssize_t len_result;
    PyObject* orig_str = args[1];
    PyObject* substr = args[2];
    PyObject* replstr = args[3];

    PyObject* maxcount = PyLong_FromLong(-1);
    if (nargs == 5 and PyNumber_Check(args[4])) {
        maxcount = PyNumber_Long(args[4]);
    }

    len_result = get_pyobject_size(orig_result);

    auto tx_taint_map = initializer->get_tainting_map();
    const auto& to_orig_str = get_tainted_object(orig_str, tx_taint_map);
    const auto& to_replstr = get_tainted_object(replstr, tx_taint_map);

    if (len_result > 0 and to_orig_str and orig_str == orig_result) {
        auto result_to = initializer->allocate_tainted_object_copy(to_orig_str);
        PyObject* new_result{ new_pyobject_id(orig_result) };
        set_tainted_object(new_result, result_to, tx_taint_map);
        Py_DECREF(orig_result);
        Py_INCREF(new_result);
        return new_result;
    }

    Py_INCREF(orig_result);
    return orig_result;

    // const auto& to_orig_str = get_tainted_object(orig_str, tx_taint_map);
    // const auto& to_replstr = get_tainted_object(replstr, tx_taint_map);

    // if (to_orig_str == nullptr or to_replstr == nullptr) {
    //     Py_DECREF(result);
    //     return result;
    // }

    // auto result_to = initializer->allocate_tainted_object_copy(to_orig_str);
    // PyObject* new_result{ new_pyobject_id(result) };
    // set_tainted_object(new_result, result_to, tx_taint_map);
    // Py_DECREF(result);
    // return new_result;
}
