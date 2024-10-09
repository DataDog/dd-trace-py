#include <Aspects/AspectStr.h>
#include <Aspects/Helpers.h>

static void
set_lengthupdated_ranges(const py::object& result, const TaintRangeRefs& ranges, const TaintRangeMapTypePtr& tx_map)
{
    if (!tx_map || tx_map->empty()) {
        return;
    }

    auto result_len = len(result);
    TaintRangeRefs copy_ranges(ranges);
    for (auto& range : copy_ranges) {
        range->length = result_len;
    }

    set_ranges(result.ptr(), copy_ranges, tx_map);
}

py::str
api_str_aspect(const py::object& orig_function,
               const int flag_added_args,
               const py::args& args,
               const py::kwargs& kwargs)
{
    py::str result_o;

    auto result_or_args = py::reinterpret_borrow<py::object>(
      process_flag_added_args(orig_function.ptr(), flag_added_args, args.ptr(), kwargs.ptr()));

    if (has_pyerr()) {
        throw py::error_already_set();
    }

    py::tuple args_tuple;
    if (py::isinstance<py::tuple>(result_or_args)) {
        args_tuple = result_or_args.cast<py::tuple>();
    } else {
        result_o = result_or_args;
        args_tuple = args;
    }
    // py::tuple args_tuple = args;

    const py::object text = args_tuple[0];

    // Call the original if not a text type
    if (not is_text(text.ptr())) {
        PyObject* as_str = PyObject_Str(text.ptr());
        if (as_str == nullptr) {
            throw py::error_already_set();
        }
        return py::reinterpret_borrow<py::str>(as_str);
    }

    py::str encoding = parse_param(1, "encoding", py::str(""), args_tuple, kwargs);
    py::str errors = parse_param(2, "errors", py::str(""), args_tuple, kwargs);

    // With no encoding we can directly call PyObject_Str
    if (len(encoding) == 0 and len(errors) == 0) {
        PyObject* as_str = PyObject_Str(text.ptr());
        if (as_str == nullptr) {
            throw py::error_already_set();
        }
        result_o = py::reinterpret_borrow<py::str>(as_str);
    } else {
        if (len(encoding) == 0) {
            // Oddly enough, the presence of just the "errors" argument is enough to trigger the decoding
            // behaviour of str() even is "encoding" is empty (but then it will take the default utf-8 value)
            encoding = "utf-8";
        }

        if (len(errors) == 0)
            errors = "strict";

        // bytes or bytearray: we have to decode
        // If it has encoding, then the text object must not be a unicode object
        if (len(encoding) > 0 and py::isinstance<py::str>(text)) {
            throw py::type_error("decoding str is not supported");
        }

        const char* char_encoding = encoding.cast<string>().c_str();
        const char* char_errors = errors.cast<string>().c_str();

        char* text_raw_bytes;
        Py_ssize_t text_raw_bytes_size;

        if (py::isinstance<py::bytearray>(text)) {
            text_raw_bytes = PyByteArray_AS_STRING(text.ptr());
            text_raw_bytes_size = PyByteArray_GET_SIZE(text.ptr());
        } else if (PyBytes_AsStringAndSize(text.ptr(), &text_raw_bytes, &text_raw_bytes_size) == -1) {
            throw py::error_already_set();
        }

        PyObject* result_pyo = PyUnicode_Decode(text_raw_bytes, text_raw_bytes_size, char_encoding, char_errors);
        if (PyErr_Occurred()) {
            throw py::error_already_set();
        }
        if (result_pyo == nullptr) {
            return py::none();
        }
        result_o = py::reinterpret_borrow<py::str>(result_pyo);
    }

    TRY_CATCH_ASPECT("str_aspect", return result_o, , {
        const auto tx_map = Initializer::get_tainting_map();
        if (!tx_map || tx_map->empty()) {
            return result_o;
        }

        auto [ranges, ranges_error] = get_ranges(text.ptr(), tx_map);
        if (ranges_error || ranges.empty()) {
            return result_o;
        }

        if (py::isinstance<py::str>(text)) {
            set_ranges(result_o.ptr(), ranges, tx_map);
        } else {
            PyObject* check_offset = PyObject_Str(text.ptr());
            if (check_offset == nullptr) {
                PyErr_Clear();
                set_lengthupdated_ranges(result_o, ranges, tx_map);
            } else {
                auto len_result_o = len(result_o);
                Py_ssize_t offset = PyUnicode_Find(result_o.ptr(), check_offset, 0, len_result_o, 1);
                if (offset == -1) {
                    PyErr_Clear();
                    set_lengthupdated_ranges(result_o, ranges, tx_map);
                } else {
                    copy_and_shift_ranges_from_strings(text, result_o, offset, len_result_o, tx_map);
                }
            }
            Py_DECREF(check_offset);
        }
        return result_o;
    });
}

void
pyexport_aspect_str(py::module& m)
{
    m.def(
      "_aspect_str",
      [](const py::object& orig_function, const int flag_added_args, const py::args& args, const py::kwargs& kwargs) {
          return api_str_aspect(orig_function, flag_added_args, args, kwargs);
      },
      "orig_function"_a = py::none(),
      "flag_added_args"_a = 0,
      py::return_value_policy::move);
}
