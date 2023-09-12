#include "AspectJoin.h"

PyObject*
aspect_join(PyObject* sep, PyObject* result, PyObject* iterable_elements, TaintRangeMapType* tx_taint_map)
{
    const size_t& len_sep = get_pyobject_size(sep);
    // FIXME: bug. if the argument is a string instead of a tuple, it will enter
    // into an infinite loop
    size_t len_iterable{ 0 };
    auto GetElement = PyList_GetItem;
    if (PyList_Check(iterable_elements)) {
        len_iterable = PyList_Size(iterable_elements);
    } else if (PyTuple_Check(iterable_elements)) {
        len_iterable = PyTuple_Size(iterable_elements);
        GetElement = PyTuple_GetItem;
    }

    unsigned long current_pos{ 0L };
    TaintedObjectPtr result_to = nullptr;
    TaintedObjectPtr first_tainted_to = nullptr;

    const auto& to_joiner = get_tainted_object(sep, tx_taint_map);

    for (size_t i = 0; i < len_iterable; i++) {
        PyObject* element = GetElement(iterable_elements, i);
        if (!element) {
            break;
        }

        // The reason of this validation is the behavior of Python2:
        // u"a".join(b"c", b"d") -> unicode
        // b"a".join(u"c", u"d") -> unicode
        // b"a".join(u"c", b"d") -> unicode
        const size_t& element_len = get_pyobject_size(element);
        if (element_len > 0) {
            const auto& to_element = get_tainted_object(element, tx_taint_map);
            if (to_element) {
                if (current_pos == 0 and !first_tainted_to) {
                    first_tainted_to = to_element;
                } else {
                    if (result_to == nullptr) {
                        // If first_tainted_to is null, it's ranges won't be copied
                        result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
                        first_tainted_to = nullptr;
                    }
                    result_to->add_ranges_shifted(to_element, current_pos);
                }
            }

            current_pos += element_len;
        }
        if (len_sep > 0 and i < len_iterable - 1 and to_joiner) {
            if (result_to == nullptr) {
                // If first_tainted_to is null, it's ranges won't be copied
                result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
                first_tainted_to = nullptr;
            }
            result_to->add_ranges_shifted(to_joiner, current_pos);
        }
        current_pos += len_sep;
    }

    if (result_to == nullptr) {
        if (first_tainted_to) {
            result_to = initializer->allocate_tainted_object_copy(first_tainted_to);
        } else {
            // No taints at all
            return result;
        }
    }

    PyObject* new_result{ new_pyobject_id(result, get_pyobject_size(result)) };
    set_tainted_object(new_result, result_to, tx_taint_map);
    Py_DECREF(result);
    return new_result;
}

PyObject*
api_join_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        // TODO: any other more sane error handling?
        return nullptr;
    }

    PyObject* sep = args[0];
    PyObject* arg0 = args[1];
    bool decref_arg0 = false;

    if (PyIter_Check(arg0) or PySet_Check(arg0) or PyFrozenSet_Check(arg0)) {
        PyObject* iterator = PyObject_GetIter(arg0);

        if (iterator != NULL) {
            PyObject* item;
            PyObject* list_aux = PyList_New(0);
            while ((item = PyIter_Next(iterator))) {
                PyList_Append(list_aux, item);
            }
            arg0 = list_aux;
            Py_DECREF(iterator);
            decref_arg0 = true;
        }
    }
    PyObject* result = nullptr;
    if (PyUnicode_Check(sep)) {
        result = PyUnicode_Join(sep, arg0);
    } else if (PyBytes_Check(sep)) {
        py::bytes result_ptr =
          py::reinterpret_borrow<py::bytes>(sep).attr("join")(py::reinterpret_borrow<py::object>(arg0));
        result = result_ptr.ptr();
        Py_INCREF(result);
    } else if (PyByteArray_Check(sep)) {
        py::bytearray result_ptr =
          py::reinterpret_borrow<py::bytearray>(sep).attr("join")(py::reinterpret_borrow<py::object>(arg0));
        result = result_ptr.ptr();
        Py_INCREF(result);
    }
    if (get_pyobject_size(result) == 0) {
        // Empty result cannot have taint ranges
        if (decref_arg0) {
            Py_DECREF(arg0);
        }
        return result;
    }

    auto ctx_map = initializer->get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
        return result;
    }
    auto res = aspect_join(sep, result, arg0, ctx_map);
    if (decref_arg0) {
        Py_DECREF(arg0);
    }
    return res;
}
