#include "AspectJoin.h"
#include "Helpers.h"

PyObject*
aspect_join_str(PyObject* sep,
                PyObject* result,
                PyObject* iterable_str,
                size_t len_iterable,
                const TaintRangeMapTypePtr& tx_taint_map)
{
    // This is the special case for unicode str and unicode iterable_str.
    // The iterable elements string will be split into 1 char-length strings.
    const auto& to_iterable_str = get_tainted_object(iterable_str, tx_taint_map);
    const auto& to_joiner = get_tainted_object(sep, tx_taint_map);

    if (to_joiner == nullptr and to_iterable_str == nullptr) {
        // No taints at all: return original result
        return result;
    }

    const size_t& len_sep = PyUnicode_GET_LENGTH(sep);
    const size_t& element_len = 1;
    unsigned long current_pos{ 0L };
    TaintedObjectPtr result_to;

    if (len_sep == 0 and to_iterable_str) {
        // Empty separator: the result is identical to iterable_str
        result_to = initializer->allocate_tainted_object_copy(to_iterable_str);
    } else if (len_sep > 0 and to_joiner and to_iterable_str == nullptr) {
        // Iterable str is not tainted, only add n-times the joiner taints
        result_to = initializer->allocate_tainted_object();
        for (size_t i = 0; i < len_iterable - 1; i++) {
            current_pos += element_len;
            result_to->add_ranges_shifted(to_joiner, current_pos);
            current_pos += len_sep;
        }
    } else {
        // General case, iterable and joiner may be tainted
        result_to = initializer->allocate_tainted_object();
        for (size_t i = 0; i < len_iterable; i++) {
            if (to_iterable_str) {
                result_to->add_ranges_shifted(to_iterable_str, current_pos, element_len, i);
            }

            current_pos += element_len;
            if (i < len_iterable - 1) {
                if (to_joiner) {
                    result_to->add_ranges_shifted(to_joiner, current_pos);
                }
                current_pos += len_sep;
            }
        }
    }

    PyObject* new_result{ new_pyobject_id(result) };
    set_tainted_object(new_result, result_to, tx_taint_map);
    Py_DecRef(result);
    return new_result;
}

PyObject*
aspect_join(PyObject* sep, PyObject* result, PyObject* iterable_elements, const TaintRangeMapTypePtr& tx_taint_map)
{
    const size_t& len_sep = get_pyobject_size(sep);

    size_t len_iterable{ 0 };
    auto GetElement = PyList_GetItem;
    if (PyList_Check(iterable_elements)) {
        len_iterable = PyList_Size(iterable_elements);
    } else if (PyTuple_Check(iterable_elements)) {
        len_iterable = PyTuple_Size(iterable_elements);
        GetElement = PyTuple_GetItem;
    } else if (PyUnicode_Check(sep) and PyUnicode_Check(iterable_elements)) {
        len_iterable = PyUnicode_GET_LENGTH(iterable_elements);
        if (len_iterable)
            return aspect_join_str(sep, result, iterable_elements, len_iterable, tx_taint_map);
        else
            return result; // Empty string is returned if empty iterable, so no tainted
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
            if (const auto& to_element = get_tainted_object(element, tx_taint_map)) {
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

    PyObject* new_result{ new_pyobject_id(result) };
    set_tainted_object(new_result, result_to, tx_taint_map);
    Py_DecRef(result);
    return new_result;
}

PyObject*
api_join_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    if (nargs != 2) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* sep = args[0];
    PyObject* arg0 = args[1];
    bool decref_arg0 = false;

    if (PyIter_Check(arg0) or PySet_Check(arg0) or PyFrozenSet_Check(arg0)) {
        PyObject* iterator = PyObject_GetIter(arg0);

        if (iterator != nullptr) {
            PyObject* item;
            PyObject* list_aux = PyList_New(0);
            while ((item = PyIter_Next(iterator))) {
                PyList_Append(list_aux, item);
            }
            arg0 = list_aux;
            Py_DecRef(iterator);
            decref_arg0 = true;
        }
    }
    PyObject* result_o = nullptr;
    if (PyUnicode_Check(sep)) {
        result_o = PyUnicode_Join(sep, arg0);
    } else if (PyBytes_Check(sep)) {
        py::bytes result_ptr =
          py::reinterpret_borrow<py::bytes>(sep).attr("join")(py::reinterpret_borrow<py::object>(arg0));
        result_o = result_ptr.ptr();
        Py_INCREF(result_o);
    } else if (PyByteArray_Check(sep)) {
        py::bytearray result_ptr =
          py::reinterpret_borrow<py::bytearray>(sep).attr("join")(py::reinterpret_borrow<py::object>(arg0));
        result_o = result_ptr.ptr();
        Py_INCREF(result_o);
    }

    if (has_pyerr()) {
        if (decref_arg0) {
            Py_DecRef(arg0);
        }
        return nullptr;
    }
    TRY_CATCH_ASPECT("join_aspect", , {
        const auto ctx_map = Initializer::get_tainting_map();
        if (not ctx_map or ctx_map->empty() or get_pyobject_size(result_o) == 0) {
            // Empty result cannot have taint ranges
            if (decref_arg0) {
                Py_DecRef(arg0);
            }
            return result_o;
        }
        auto res = aspect_join(sep, result_o, arg0, ctx_map);
        if (decref_arg0) {
            Py_DecRef(arg0);
        }
        return res;
    });
}
