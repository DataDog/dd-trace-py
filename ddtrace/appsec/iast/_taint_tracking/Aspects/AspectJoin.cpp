#include "AspectJoin.h"

PyObject* aspect_join(const PyObject* sep,
                                 PyObject* result,
                                 PyObject* iterable_tuple,
                                 TaintRangeMapType* tx_taint_map) {
    const size_t& len_sep = PyUnicode_GET_LENGTH(sep);
    // FIXME: bug. if the argument is a string instead of a tuple, it will enter into an infinite loop
    const size_t& len_tuple = PyTuple_Size(iterable_tuple);
    unsigned long current_pos{0L};
    TaintedObjectPtr result_to = nullptr;
    TaintedObjectPtr first_tainted_to = nullptr;

    const auto& to_joiner = get_tainted_object(sep, tx_taint_map);

    for (size_t i = 0; i < len_tuple; i++) {
        PyObject* element = PyTuple_GetItem(iterable_tuple, i);
        if (!element) {
            break;
        }

        // The reason of this validation is the behavior of Python2:
        // u"a".join(b"c", b"d") -> unicode
        // b"a".join(u"c", u"d") -> unicode
        // b"a".join(u"c", b"d") -> unicode

        const size_t& element_len = PyUnicode_GET_LENGTH(element);
        if (element_len > 0) {
            const auto& to_element = get_tainted_object(element, tx_taint_map);
            if (to_element) {
                if (current_pos == 0 and !first_tainted_to) {
                    first_tainted_to = to_element;
                } else {
                    if (!result_to) {
                        // If first_tainted_to is null, it's ranges won't be copied
                        result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
                        first_tainted_to = nullptr;
                    }
                    result_to->add_ranges_shifted(to_element, current_pos);
                }
            }

            current_pos += element_len;
        }
        if (len_sep > 0 and i < len_tuple - 1 and to_joiner) {
            if (!result_to) {
                // If first_tainted_to is null, it's ranges won't be copied
                result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
                first_tainted_to = nullptr;
            }
            result_to->add_ranges_shifted(to_joiner, current_pos);
        }
        current_pos += len_sep;
    }

    if (!result_to) {
        if (first_tainted_to) {
            result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
        } else {
            // No taints at all
            return result;
        }
    }

    PyObject* new_result{new_pyobject_id(result, PyUnicode_GET_LENGTH(result))};
    //set_tainted_object(new_result, result_to, tx_taint_map);

    return result;
}

PyObject* api_join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    if (nargs != 2) {
        // TODO: any other more sane error handling?
        return nullptr;
    }

    PyObject* sep = args[0];
    PyObject* arg0 = args[1];
    PyObject* result = PyUnicode_Join(sep, arg0);

    if (PyUnicode_GET_LENGTH(result) == 0) {
        // Empty result cannot have taint ranges
        return result;
    }
    auto ctx_map = initializer->get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
    return result;
    }
    return aspect_join(sep, result, arg0, ctx_map);
}