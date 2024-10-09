#include <Aspects/AspectStr.h>
#include <Aspects/Helpers.h>

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

PyObject*
api_str_aspect(PyObject* self, PyObject* const* args, const Py_ssize_t nargs, PyObject* kwnames)
{
    if (nargs < 3 or nargs > 5) {
        py::set_error(PyExc_ValueError, MSG_ERROR_N_PARAMS);
        return nullptr;
    }

    PyObject* orig_function = args[0];
    PyObject* py_flag_added_args = args[1];
    PyObject* text = args[2];
    PyObject* pyo_encoding = nullptr;
    PyObject* pyo_errors = nullptr;

    if (nargs > 3)
        pyo_encoding = args[3];
    if (nargs > 4)
        pyo_errors = args[4];

    if (kwnames and PyTuple_Check(kwnames)) {
        for (Py_ssize_t i = 0; i < PyTuple_Size(kwnames); i++) {
            PyObject* key = PyTuple_GET_ITEM(kwnames, i); // Keyword name
            PyObject* value = args[nargs + i];            // Keyword value
            if (PyUnicode_CompareWithASCIIString(key, "encoding") == 0) {
                pyo_encoding = value;
                continue;
            }
            if (PyUnicode_CompareWithASCIIString(key, "errors") == 0) {
                pyo_errors = value;
            }
        }
    }

    bool has_encoding = pyo_encoding != nullptr and PyUnicode_GetLength(pyo_encoding) > 0;
    bool has_errors = pyo_errors != nullptr and PyUnicode_GetLength(pyo_errors) > 0;

    const char* encoding = has_encoding ? PyUnicode_AsUTF8(pyo_encoding) : "";
    if (encoding == nullptr)
        encoding = "";

    const char* errors = has_errors ? PyUnicode_AsUTF8(pyo_errors) : "";
    if (errors == nullptr)
        errors = "";

    PyObject* result_o = nullptr;
    // FIXME?
    // const auto flag_added_args = PyLong_Check(py_flag_added_args) ? PyLong_AsLong(py_flag_added_args) : 0L;
    // const auto result_or_args = process_flag_added_args(orig_function, flag_added_args, *args, nullptr);
    //
    //
    // if (not PyTuple_Check(result_or_args)) {
    //     return result_or_args;
    // }

    if (has_pyerr()) {
        throw py::error_already_set();
    }

    // Call the original if not a text type
    if (not is_text(text)) {
        PyObject* as_str = PyObject_Str(text);
        if (as_str == nullptr) {
            throw py::error_already_set();
        }
        return as_str;
    }

    // With no encoding or errors arguments we can directly call PyObject_Str, which is faster
    if (!has_encoding and !has_errors) {
        result_o = PyObject_Str(text);
        if (result_o == nullptr) {
            throw py::error_already_set();
        }
    } else {
        if (!has_encoding) {
            // Oddly enough, the presence of just the "errors" argument is enough to trigger the decoding
            // behaviour of str() even is "encoding" is empty (but then it will take the default utf-8 value)
            encoding = "utf-8";
        }

        if (!has_errors)
            errors = "strict";

        // bytes or bytearray: we have to decode
        // If it has encoding, then the text object must not be a unicode object
        if (has_encoding and PyUnicode_Check(text)) {
            throw py::type_error("decoding str is not supported");
        }

        char* text_raw_bytes;
        Py_ssize_t text_raw_bytes_size;

        if (PyByteArray_Check(text)) {
            text_raw_bytes = PyByteArray_AS_STRING(text);
            text_raw_bytes_size = PyByteArray_GET_SIZE(text);
        } else if (PyBytes_AsStringAndSize(text, &text_raw_bytes, &text_raw_bytes_size) == -1) {
            throw py::error_already_set();
        }

        result_o = PyUnicode_Decode(text_raw_bytes, text_raw_bytes_size, encoding, errors);
        if (PyErr_Occurred()) {
            throw py::error_already_set();
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

        // JJJ optimize
        auto len_result_o = py::len(py::reinterpret_borrow<py::object>(result_o));

        if (PyUnicode_Check(text)) {
            set_ranges(result_o, ranges, tx_map);
        } else {
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
