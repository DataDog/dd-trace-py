#include "AspectReplace.h"
#include "Helpers.h"

PyObject*
api_replace_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    PyObject* orig_result = args[0];
    Py_ssize_t result_len = get_pyobject_size(orig_result);
    PyObject* orig_str = args[1];
    PyObject* substr = args[2];
    PyObject* replstr = args[3];

    if (result_len == 0) {
        Py_INCREF(orig_result);
        return orig_result;
    }

    Py_ssize_t origstr_len = get_pyobject_size(substr);
    Py_ssize_t substr_len = get_pyobject_size(substr);
    Py_ssize_t replstr_len = get_pyobject_size(replstr);

    if (get_pyobject_size(self) < substr_len) {
        /* nothing to do; return the original bytes */
        Py_INCREF(orig_result);
        return orig_result;
    }

    PyObject* maxcount = PyLong_FromLong(-1);
    if (nargs == 5 and PyNumber_Check(args[4])) {
        maxcount = PyNumber_Long(args[4]);
    }

    if (maxcount == 0) {
        /* nothing to do; return the original bytes */
        Py_INCREF(orig_result);
        return orig_result;
    }

    auto tx_taint_map = initializer->get_tainting_map();
    const auto& to_orig_str = get_tainted_object(orig_str, tx_taint_map);
    const auto& to_replstr = get_tainted_object(replstr, tx_taint_map);

    if (!to_replstr and !to_orig_str) {
        Py_INCREF(orig_result);
        return orig_result;
    }

    if (substr_len == 0 and replstr_len == 0) {
        /* nothing to do; return the original bytes */
        Py_INCREF(orig_result);
        return orig_result;
    }

    TaintRangePtr* orig_str_to_array = get_tainted_ranges_array(orig_str, tx_taint_map);
    TaintRangePtr* replstr_to_array = get_tainted_ranges_array(replstr, tx_taint_map);

    /* Handle zero-length special cases */
    if (substr_len == 0) {
        // TODO:
        /* insert the 'to' bytes everywhere.    */
        /*    >>> b"Python".replace(b"", b".")  */
        /*    b'.P.y.t.h.o.n.'                  */
        // return stringlib_replace_interleave(self, to_s, to_len, maxcount);
        TaintedObjectPtr tainted;
        if (to_replstr) {
            tainted = initializer->allocate_tainted_object_copy(to_replstr);
        } else {
            tainted = initializer->allocate_tainted_object();
        }
        TaintRangeRefs ranges = tainted->get_ranges();
        for (Py_ssize_t i = 0; i < origstr_len; i++) {
            TaintRangePtr curr_tr = orig_str_to_array[i];
            if (curr_tr != nullptr) {
                TaintRangePtr repositioned_tr = reposition_and_limit_taint_range(curr_tr, i, 1);
                ranges.emplace_back(repositioned_tr);
            }

            if (to_replstr) {
                tainted->add_ranges_shifted(to_replstr->get_ranges(), i + 1, -1, -1);
            }
        }
        delete[] orig_str_to_array;
        delete[] replstr_to_array;
        PyObject* new_result{ new_pyobject_id(orig_result) };
        set_tainted_object(new_result, tainted, tx_taint_map);
        Py_INCREF(new_result);
        return new_result;
    }

    // if (replstr_len == 0) {
    //     /* delete all occurrences of 'from' bytes */
    //     // TODO: only keep "self" tainted ranges removing characters

    //     if (substr_len == 1) {
    //         return stringlib_replace_delete_single_character(self, from_s[0], maxcount);
    //     } else {
    //         return stringlib_replace_delete_substring(self, from_s, from_len, maxcount);
    //     }
    // }

    // // TODO
    // /* Handle special case where both bytes have the same length */
    // if (substr_len == replstr_len) {
    //     if (substr_len == 1) {
    //         return stringlib_replace_single_character_in_place(self, substr[0], replstr[0], maxcount);
    //     } else {
    //         return stringlib_replace_substring_in_place(self, substr, substr_len, replstr, replstr_len, maxcount);
    //     }
    // }

    // // TODO
    // /* Otherwise use the more generic algorithms */
    // if (from_len == 1) {
    //     return stringlib_replace_single_character(self, from_s[0], to_s, to_len, maxcount);
    // } else {
    //     /* len('from')>=2, len('to')>=1 */
    //     return stringlib_replace_substring(self, from_s, from_len, to_s, to_len, maxcount);
    // }

    delete[] orig_str_to_array;
    delete[] replstr_to_array;

    if (!to_replstr and orig_result == replstr) {
        Py_INCREF(orig_result);
        return orig_result;
    }
    if (to_orig_str and orig_str == orig_result) {
        auto result_to = initializer->allocate_tainted_object_copy(to_orig_str);
        PyObject* new_result{ new_pyobject_id(orig_result) };
        set_tainted_object(new_result, result_to, tx_taint_map);
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
    //
    //
    //
    // Algorithm based on stringlib_replace:
    // https://github.com/python/cpython/blob/f27b83090701b9c215e0d65f1f924fb9330cb649/Objects/stringlib/transmogrify.h#L676C1-L737C2
}
