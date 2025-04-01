#include "aspect_str.h"
#include "helpers.h"

static void
set_lengthupdated_ranges(PyObject* result,
                         Py_ssize_t result_len,
                         const TaintRangeRefs& ranges,
                         const TaintRangeMapTypePtr& tx_map)
{
    if (!tx_map || tx_map->empty()) {
        return;
    }

    TaintRangeRefs copy_ranges(ranges);
    for (auto& range : copy_ranges) {
        range->length = result_len;
    }

    set_ranges(result, copy_ranges, tx_map);
}

static PyObject*
call_original_function(PyObject* orig_function,
                       int nargs,
                       int flag_added_args,
                       PyObject* const* args,
                       PyObject* kwnames)
{
    int skip_args = 2 + flag_added_args;

    // convert ** args to *args
    py::list py_args_list;
    for (Py_ssize_t i = skip_args; i < nargs; ++i) {
        py_args_list.append(py::reinterpret_borrow<py::object>(args[i]));
    }
    py::args py_args(py_args_list);

    PyObject* kwargs = kwnames_to_kwargs(args, nargs, kwnames);
    auto res = PyObject_Call(orig_function, py_args.ptr(), kwnames_to_kwargs(args, nargs, kwnames));
    Py_DECREF(kwargs);
    return res;
}

static std::tuple<int, int, PyObject*, PyObject*, PyObject*, PyObject*>
get_args(PyObject* const* args, const Py_ssize_t nargs, PyObject* kwnames)
{
    int flag_added_args = -1;

    PyObject* orig_function = args[0];

    if (nargs > 1) {
        if (PyLong_Check(args[1])) {
            flag_added_args = PyLong_AsLong(args[1]);
        }
    }

    PyObject* text = nullptr;
    if (nargs > 2) {
        text = args[2];
    }

    PyObject* pyo_encoding = nullptr;
    PyObject* pyo_errors = nullptr;
    int effective_args = 1;

    if (nargs > 3) {
        pyo_encoding = args[3];
        effective_args = 2;
    }
    if (nargs > 4) {
        pyo_errors = args[4];
        effective_args = 3;
    }

    // Not using kwnames to kwargs here for performance
    if (kwnames and PyTuple_Check(kwnames)) {
        for (Py_ssize_t i = 0; i < PyTuple_Size(kwnames); i++) {
            if (effective_args > 3) {
                // Will produce an error, so end here
                break;
            }
            PyObject* key = PyTuple_GET_ITEM(kwnames, i); // Keyword name
            PyObject* value = args[nargs + i];            // Keyword value
            if (PyUnicode_CompareWithASCIIString(key, "encoding") == 0) {
                pyo_encoding = value;
                ++effective_args;
                continue;
            }
            if (PyUnicode_CompareWithASCIIString(key, "errors") == 0) {
                ++effective_args;
                pyo_errors = value;
            }

            if (pyo_encoding and pyo_errors) {
                break;
            }
        }
    }

    return { effective_args, flag_added_args, orig_function, text, pyo_encoding, pyo_errors };
}

PyObject*
api_str_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs, PyObject* kwnames)
{
    if (nargs < 2 or nargs > 5) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    auto [effective_args, flag_added_args, orig_function, text, pyo_encoding, pyo_errors] =
      get_args(args, nargs, kwnames);

    // This is a flag that the function was not the original
    if (flag_added_args == -1 or not is_pointer_this_builtin(orig_function, "str")) {
        return call_original_function(orig_function, nargs, flag_added_args, args, kwnames);
    }

    if (nargs == 2 or (nargs > 2 and PyUnicode_Check(args[2]) and PyObject_Length(args[2]) == 0)) {
        // builtin str() without parameters or empty parameter, just return an empty string
        return PyUnicode_FromString("");
    }

    if (effective_args > 3) {
        string error_msg = "str() takes at most 3 arguments (" + to_string(effective_args) + " given)";
        py::set_error(PyExc_TypeError, error_msg.c_str());
        return nullptr;
    }

    const bool has_encoding = pyo_encoding != nullptr and PyUnicode_GetLength(pyo_encoding) > 0;
    const bool has_errors = pyo_errors != nullptr and PyUnicode_GetLength(pyo_errors) > 0;

    // If it has encoding, then the text object must be a bytes or bytearray object; if not, call the original
    // function so the error is raised
    if (has_encoding and (not PyByteArray_Check(text) and not PyBytes_Check(text))) {
        return call_original_function(orig_function, nargs, flag_added_args, args, kwnames);
    }

    // Call the original if not a text type and has no encoding
    if (not is_text(text)) {
        PyObject* as_str = PyObject_Str(text);
        return as_str;
    }

    PyObject* result_o = nullptr;

    // With no encoding or errors arguments we can directly call PyObject_Str, which is faster
    if (!has_encoding and !has_errors) {
        result_o = PyObject_Str(text);
        if (result_o == nullptr) {
            return nullptr;
        }
    } else {
        // Oddly enough, the presence of just the "errors" argument is enough to trigger the decoding
        // behaviour of str() even is "encoding" is empty (but then it will take the default utf-8 value)
        char* text_raw_bytes = nullptr;
        Py_ssize_t text_raw_bytes_size;

        if (PyByteArray_Check(text)) {
            text_raw_bytes = PyByteArray_AS_STRING(text);
            text_raw_bytes_size = PyByteArray_GET_SIZE(text);
        } else if (PyBytes_AsStringAndSize(text, &text_raw_bytes, &text_raw_bytes_size) == -1) {
            if (has_pyerr()) {
                return nullptr;
            }
            throw py::error_already_set();
        }

        const char* encoding = has_encoding ? PyUnicode_AsUTF8(pyo_encoding) : "utf-8";
        const char* errors = has_errors ? PyUnicode_AsUTF8(pyo_errors) : "strict";
        result_o = PyUnicode_Decode(text_raw_bytes, text_raw_bytes_size, encoding, errors);

        if (PyErr_Occurred()) {
            return nullptr;
        }
        if (result_o == nullptr) {
            Py_RETURN_NONE;
        }
    }

    TRY_CATCH_ASPECT("str_aspect", return result_o, , {
        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
            return result_o;
        }

        auto [ranges, ranges_error] = get_ranges(text, tx_map);
        if (ranges_error || ranges.empty()) {
            return result_o;
        }

        if (PyUnicode_Check(text)) {
            set_ranges(result_o, ranges, tx_map);
        } else {
            // Encoding on Bytes or Bytearray: size could change
            const auto len_result_o = PyObject_Length(result_o);
            PyObject* check_offset = PyObject_Str(text);

            if (check_offset == nullptr) {
                PyErr_Clear();
                set_lengthupdated_ranges(result_o, len_result_o, ranges, tx_map);
            } else {
                Py_ssize_t offset = PyUnicode_Find(result_o, check_offset, 0, len_result_o, 1);
                if (offset == -1) {
                    PyErr_Clear();
                    set_lengthupdated_ranges(result_o, len_result_o, ranges, tx_map);
                } else {
                    copy_and_shift_ranges_from_strings(text, result_o, offset, len_result_o, tx_map);
                }
            }
            Py_DECREF(check_offset);
        }
        return result_o;
    });
}
