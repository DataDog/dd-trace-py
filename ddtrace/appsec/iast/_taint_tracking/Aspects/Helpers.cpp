#include "Helpers.h"
#include <iostream>// JJJ: remove

using namespace pybind11::literals;
namespace py = pybind11;

size_t
get_pyobject_size(PyObject *obj) {
    size_t len_candidate_text;
    if (PyUnicode_Check(obj)) {
        len_candidate_text = PyUnicode_GET_LENGTH(obj);
    } else if (PyBytes_Check(obj)) {
        len_candidate_text = PyBytes_Size(obj);
    } else if (PyByteArray_Check(obj)) {
        len_candidate_text = PyByteArray_Size(obj);
    }
    return len_candidate_text;
}

template<class StrType>
StrType
common_replace(const py::str &string_method,
               const StrType &candidate_text,
               const py::args &args,
               const py::kwargs &kwargs) {
    TaintRangeRefs candidate_text_ranges{get_ranges(candidate_text.ptr())};

    StrType res = py::getattr(candidate_text, string_method)(*args, **kwargs);
    if (candidate_text_ranges.empty()) {
        return res;
    }

    set_ranges(res.ptr(), api_shift_taint_ranges(candidate_text_ranges, 0));
    return res;
}

#pragma clang diagnostic push
#pragma ide diagnostic ignored "misc-no-recursion"

void pyexport_aspect_helpers(py::module &m) {
    m.def("common_replace", &common_replace<py::bytes>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::str>, "string_method"_a, "candidate_text"_a);
    m.def("common_replace", &common_replace<py::bytearray>, "string_method"_a, "candidate_text"_a);
}
