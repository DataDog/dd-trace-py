#include "taint_range.h"
#include "initializer/initializer.h"
#include "utils/string_utils.h"

namespace py = pybind11;

void
TaintRange::reset()
{
    source.reset();
    secure_marks = 0;
    start = 0;
    length = 0;
};

string
TaintRange::toString() const
{
    ostringstream ret;
    ret << "TaintRange at " << this << " "
        << "[start=" << start << ", length=" << length << " source=" << source.toString() << "]";
    return ret.str();
}

TaintRange::operator std::string() const
{
    return toString();
}

// Note: don't use size_t or long, if the hash is bigger than an int, Python
// will re-hash it!
uint
TaintRange::get_hash() const
{
    const uint hstart = hash<uint>()(this->start);
    const uint hlength = hash<uint>()(this->length);
    const uint hsource = hash<uint>()(this->source.get_hash());
    return hstart ^ hlength ^ hsource;
};

void
TaintRange::add_secure_mark(VulnerabilityType mark)
{
    secure_marks |= (1ULL << static_cast<uint64_t>(mark));
}

bool
TaintRange::has_secure_mark(VulnerabilityType mark) const
{
    return (secure_marks & (1ULL << static_cast<uint64_t>(mark))) != 0;
}

TaintRangePtr
shift_taint_range(const TaintRangePtr& source_taint_range, const RANGE_START offset, const RANGE_LENGTH new_length = -1)
{
    const auto new_length_to_use = new_length == -1 ? source_taint_range->length : new_length;
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset,
                                                  new_length_to_use,
                                                  source_taint_range->source,
                                                  source_taint_range->secure_marks);
    return tptr;
}

TaintRangeRefs
shift_taint_ranges(const TaintRangeRefs& source_taint_ranges,
                   const RANGE_START offset,
                   const RANGE_LENGTH new_length = -1)
{
    TaintRangeRefs new_ranges;
    new_ranges.reserve(source_taint_ranges.size());

    for (const auto& trange : source_taint_ranges) {
        new_ranges.emplace_back(shift_taint_range(trange, offset, new_length));
    }
    return new_ranges;
}

TaintRangeRefs
api_shift_taint_ranges(const TaintRangeRefs& source_taint_ranges,
                       const RANGE_START offset,
                       const RANGE_LENGTH new_length = -1)
{
    return shift_taint_ranges(source_taint_ranges, offset);
}

py::object
api_set_ranges(py::handle& str, const TaintRangeRefs& ranges)
{
    const auto tx_map = Initializer::get_tainting_map();

    if (not tx_map) {
        throw py::value_error(MSG_ERROR_TAINT_MAP);
    }
    set_ranges(str.ptr(), ranges, tx_map);
    return py::none();
}

/**
 * set_ranges_from_values.
 *
 * The equivalent Python script of this function is:
 *  ```
 *  api_set_ranges_from_values
 *  pyobject_newid = new_pyobject_id(pyobject)
 *  source = Source(source_name, source_value, source_origin)
 *  pyobject_range = TaintRange(0, len(pyobject), source)
 *  set_ranges(pyobject_newid, [pyobject_range])
 *  ```
 *
 * @param self The Python extension module.
 * @param args An array of Python objects containing the candidate text and text aspect.
 *   @param args[0] PyObject, string to set the ranges
 *   @param args[1] long. Length of the string
 *   @param args[2] string. source name
 *   @param args[3] string. source value
 *   @param args[4] int. origin type
 * @param nargs The number of arguments in the 'args' array.
 */
PyObject*
api_set_ranges_from_values(PyObject* self, PyObject* const* args, const Py_ssize_t nargs)
{
    bool result = false;
    const char* result_error_msg = MSG_ERROR_N_PARAMS;
    PyObject* pyobject_n = nullptr;

    if (nargs == 5) {
        PyObject* tainted_object = args[0];
        const auto tx_map = Initializer::get_tainting_map();
        if (not tx_map) {
            py::set_error(PyExc_ValueError, MSG_ERROR_TAINT_MAP);
            return nullptr;
        }

        pyobject_n = new_pyobject_id(tainted_object);
        PyObject* len_pyobject_py = args[1];

        const long len_pyobject = PyLong_AsLong(len_pyobject_py);
        if (const string source_name = PyObjectToString(args[2]); not source_name.empty()) {
            if (const string source_value = PyObjectToString(args[3]); not source_value.empty()) {
                const auto source_origin = static_cast<OriginType>(PyLong_AsLong(args[4]));
                const auto source = Source(source_name, source_value, source_origin);
                const auto range = initializer->allocate_taint_range(0, len_pyobject, source, {});
                const auto ranges = vector{ range };
                result = set_ranges(pyobject_n, ranges, tx_map);
                if (not result) {
                    result_error_msg = MSG_ERROR_SET_RANGES;
                }
            } else {
                result_error_msg = "iast::propagation::native::Invalid or empty source_value";
            }
        } else {
            result_error_msg = "[iast::propagation::native::Invalid or empty source_name";
        }
    }
    if (not result) {
        py::set_error(PyExc_ValueError, result_error_msg);
        return nullptr;
    }

    return pyobject_n;
}

std::pair<TaintRangeRefs, bool>
get_ranges(PyObject* string_input, const TaintRangeMapTypePtr& tx_map)
{
    TaintRangeRefs result;
    if (not is_tainteable(string_input)) {
        return std::make_pair(result, true);
    }

    if (tx_map->empty()) {
        return std::make_pair(result, false);
    }
    const auto it = tx_map->find(get_unique_id(string_input));
    if (it == tx_map->end()) {
        return std::make_pair(result, false);
    }

    if (get_internal_hash(string_input) != it->second.first) {
        tx_map->erase(it);
        return std::make_pair(result, false);
    }

    return std::make_pair(it->second.second->get_ranges(), false);
}

bool
set_ranges(PyObject* str, const TaintRangeRefs& ranges, const TaintRangeMapTypePtr& tx_map)
{
    if (ranges.empty()) {
        return false;
    }
    auto obj_id = get_unique_id(str);
    const auto it = tx_map->find(obj_id);
    auto new_tainted_object = initializer->allocate_ranges_into_taint_object(ranges);

    set_fast_tainted_if_notinterned_unicode(str);
    if (it != tx_map->end()) {
        it->second.second.reset();
        it->second = std::make_pair(get_internal_hash(str), new_tainted_object);
        return true;
    }

    tx_map->insert({ obj_id, std::make_pair(get_internal_hash(str), new_tainted_object) });
    return true;
}

// Returns a tuple with (all ranges, ranges of candidate_text)
// FIXME: Take a PyList as parameter_list instead of a py::tuple (same for the
// result)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(PyObject* candidate_text, const py::tuple& parameter_list)
{
    if (not is_tainteable(candidate_text))
        return {};

    TaintRangeRefs all_ranges;
    const auto tx_map = Initializer::get_tainting_map();
    if (not tx_map or tx_map->empty()) {
        return { {}, {} };
    }

    auto [candidate_text_ranges, ranges_error] = get_ranges(candidate_text, tx_map);
    if (not ranges_error) {
        for (const auto& param_handler : parameter_list) {
            if (const auto param = param_handler.cast<py::object>().ptr(); is_tainteable(param)) {
                if (auto [ranges, ranges_error] = get_ranges(param, tx_map); not ranges_error) {
                    all_ranges.insert(all_ranges.end(), ranges.begin(), ranges.end());
                }
            }
        }
        all_ranges.insert(all_ranges.end(), candidate_text_ranges.begin(), candidate_text_ranges.end());
    }
    return { all_ranges, candidate_text_ranges };
}

TaintRangePtr
get_range_by_hash(const size_t range_hash, optional<TaintRangeRefs>& taint_ranges)
{
    if (not taint_ranges or !taint_ranges.has_value() or taint_ranges->empty()) {
        return nullptr;
    }
    // TODO: Replace this loop with a efficient function, vector.find() is O(n) too.
    TaintRangePtr null_range = nullptr;
    // taint_ranges.value throws a compile error: 'value' is unavailable: introduced in macOS 10.13
    for (const auto& range : *taint_ranges) {
        if (range_hash == range->get_hash()) {
            return range;
        }
    }
    return null_range;
}

TaintRangeRefs
api_get_ranges(const py::handle& string_input)
{
    const auto tx_map = Initializer::get_tainting_map();

    if (not tx_map) {
        throw py::value_error(MSG_ERROR_TAINT_MAP);
    }

    auto [ranges, ranges_error] = get_ranges(string_input.ptr(), tx_map);
    if (ranges_error) {
        throw py::value_error(MSG_ERROR_GET_RANGES_TYPE);
    }
    return ranges;
}

void
api_copy_ranges_from_strings(py::handle& str_1, py::handle& str_2)
{
    const auto tx_map = Initializer::get_tainting_map();

    if (not tx_map) {
        py::set_error(PyExc_ValueError, MSG_ERROR_TAINT_MAP);
        return;
    }

    auto [ranges, ranges_error] = get_ranges(str_1.ptr(), tx_map);
    if (ranges_error) {
        py::set_error(PyExc_TypeError, MSG_ERROR_TAINT_MAP);
        return;
    }
    if (const bool result = set_ranges(str_2.ptr(), ranges, tx_map); not result) {
        py::set_error(PyExc_TypeError, MSG_ERROR_SET_RANGES);
    }
}

inline void
api_copy_and_shift_ranges_from_strings(py::handle& str_1,
                                       py::handle& str_2,
                                       const int offset,
                                       const int new_length = -1)
{
    const auto tx_map = Initializer::get_tainting_map();
    if (not tx_map) {
        py::set_error(PyExc_ValueError, MSG_ERROR_TAINT_MAP);
        return;
    }
    auto [ranges, ranges_error] = get_ranges(str_1.ptr(), tx_map);
    if (ranges_error) {
        py::set_error(PyExc_TypeError, MSG_ERROR_TAINT_MAP);
        return;
    }
    if (const bool result = set_ranges(str_2.ptr(), shift_taint_ranges(ranges, offset, new_length), tx_map);
        not result) {
        py::set_error(PyExc_TypeError, MSG_ERROR_SET_RANGES);
    }
}

TaintedObjectPtr
get_tainted_object(PyObject* str, const TaintRangeMapTypePtr& tx_map)
{
    if (not str)
        return nullptr;

    if (is_notinterned_notfasttainted_unicode(str) or tx_map->empty()) {
        return nullptr;
    }

    const auto it = tx_map->find(get_unique_id(str));
    if (it == tx_map->end()) {
        return nullptr;
    }

    if (get_internal_hash(str) != it->second.first) {
        tx_map->erase(it);
        return nullptr;
    }
    return it == tx_map->end() ? nullptr : it->second.second;
}

Py_hash_t
bytearray_hash(PyObject* bytearray)
{
    // Bytearrays don't have hash by default so we will generate one by getting the internal str hash
    auto str = py::str(bytearray);
    return PyObject_Hash(str.ptr());
}

Py_hash_t
get_internal_hash(PyObject* obj)
{
    // Shortcut check to avoid the slower checks for bytearray and re.match objects
    if (PyUnicode_Check(obj) || PyBytes_Check(obj)) {
        return PyObject_Hash(obj);
    }

    if (PyByteArray_Check(obj)) {
        return bytearray_hash(obj);
    }

    if (PyReMatch_Check(obj)) {
        // Use the match.string for hashing
        PyObject* string_obj = PyObject_GetAttrString(obj, "string");
        if (string_obj == nullptr) {
            return PyObject_Hash(obj);
        }
        const auto hash = PyObject_Hash(string_obj);
        Py_DECREF(string_obj);
        return hash;
    }

    return PyObject_Hash(obj);
}

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, const TaintRangeMapTypePtr& tx_map)
{
    if (not str or not is_tainteable(str)) {
        return;
    }
    auto obj_id = get_unique_id(str);
    set_fast_tainted_if_notinterned_unicode(str);
    if (const auto it = tx_map->find(obj_id); it != tx_map->end()) {
        // The same memory address was probably re-used for a different PyObject, so
        // we need to overwrite it.
        if (it->second.second != tainted_object) {
            // If the tainted object is different, we need to decref the previous one
            // and incref the new one. But if it's the same object, we can avoid both
            // operations, since they would be redundant.
            it->second.second.reset();
            it->second = std::make_pair(get_internal_hash(str), tainted_object);
        }
        // Update the hash, because for bytearrays it could have changed after the extend operation
        it->second.first = get_internal_hash(str);
        return;
    }
    tx_map->insert({ obj_id, std::make_pair(get_internal_hash(str), tainted_object) });
}

// OPTIMIZATION TODO: export the variant of these functions taking a PyObject*
// using the C API directly.
void
pyexport_taintrange(py::module& m)
{
    m.def("is_notinterned_notfasttainted_unicode",
          py::overload_cast<const PyObject*>(&is_notinterned_notfasttainted_unicode),
          "candidate_text"_a);
    m.def("is_notinterned_notfasttainted_unicode", &api_is_unicode_and_not_fast_tainted, "candidate_text"_a);

    m.def("set_fast_tainted_if_notinterned_unicode",
          py::overload_cast<PyObject*>(&set_fast_tainted_if_notinterned_unicode),
          "candidate_text"_a);
    m.def("set_fast_tainted_if_notinterned_unicode", &api_set_fast_tainted_if_unicode, "text"_a);

    m.def("are_all_text_all_ranges",
          &api_are_all_text_all_ranges,
          "candidate_text"_a,
          "parameter_list"_a,
          py::return_value_policy::move);

    m.def("shift_taint_range",
          &api_shift_taint_range,
          py::return_value_policy::move,
          "source_taint_range"_a,
          "offset"_a,
          "new_length"_a = -1);
    m.def("shift_taint_ranges",
          &api_shift_taint_ranges,
          py::return_value_policy::move,
          "ranges"_a,
          "offset"_a,
          "new_length"_a = -1);

    // m.def("set_ranges", py::overload_cast<PyObject*, const TaintRangeRefs&>(&api_set_ranges), "str"_a, "ranges"_a);
    m.def("set_ranges", &api_set_ranges, "str"_a, "ranges"_a);
    m.def("copy_ranges_from_strings", &api_copy_ranges_from_strings, "str_1"_a, "str_2"_a);
    m.def("copy_and_shift_ranges_from_strings",
          &api_copy_and_shift_ranges_from_strings,
          "str_1"_a,
          "str_2"_a,
          "offset"_a,
          "new_length"_a = -1);

    m.def("get_ranges", &api_get_ranges, "string_input"_a, py::return_value_policy::take_ownership);

    m.def("get_range_by_hash",
          &get_range_by_hash,
          "range_hash"_a,
          "taint_ranges"_a,
          py::return_value_policy::take_ownership);

    // Fake constructor, used to force calling allocate_taint_range for performance reasons
    m.def(
      "taint_range",
      [](
        const RANGE_START start, const RANGE_LENGTH length, const Source& source, const SecureMarks& secure_marks = 0) {
          return initializer->allocate_taint_range(start, length, source, secure_marks);
      },
      "start"_a,
      "length"_a,
      "source"_a,
      "secure_marks"_a = 0,
      py::return_value_policy::move);

    py::enum_<VulnerabilityType>(m, "VulnerabilityType")
      .value("CODE_INJECTION", VulnerabilityType::CODE_INJECTION)
      .value("COMMAND_INJECTION", VulnerabilityType::COMMAND_INJECTION)
      .value("HEADER_INJECTION", VulnerabilityType::HEADER_INJECTION)
      .value("INSECURE_COOKIE", VulnerabilityType::INSECURE_COOKIE)
      .value("NO_HTTPONLY_COOKIE", VulnerabilityType::NO_HTTPONLY_COOKIE)
      .value("NO_SAMESITE_COOKIE", VulnerabilityType::NO_SAMESITE_COOKIE)
      .value("PATH_TRAVERSAL", VulnerabilityType::PATH_TRAVERSAL)
      .value("SQL_INJECTION", VulnerabilityType::SQL_INJECTION)
      .value("SSRF", VulnerabilityType::SSRF)
      .value("STACKTRACE_LEAK", VulnerabilityType::STACKTRACE_LEAK)
      .value("WEAK_CIPHER", VulnerabilityType::WEAK_CIPHER)
      .value("WEAK_HASH", VulnerabilityType::WEAK_HASH)
      .value("WEAK_RANDOMNESS", VulnerabilityType::WEAK_RANDOMNESS)
      .value("XSS", VulnerabilityType::XSS)
      .export_values();

    py::class_<TaintRange, shared_ptr<TaintRange>>(m, "TaintRange_")
      // Normal constructor disabled on the Python side, see above
      .def_readonly("start", &TaintRange::start)
      .def_readonly("length", &TaintRange::length)
      .def_readonly("source", &TaintRange::source)
      .def_readonly("secure_marks", &TaintRange::secure_marks)
      .def("__str__", &TaintRange::toString)
      .def("__repr__", &TaintRange::toString)
      .def("__hash__", &TaintRange::get_hash)
      .def("get_hash", &TaintRange::get_hash)
      .def("add_secure_mark", &TaintRange::add_secure_mark)
      .def("__eq__",
           [](const TaintRangePtr& self, const TaintRangePtr& other) {
               if (other == nullptr)
                   return false;
               return self->start == other->start && self->length == other->length;
           })
      .def("__ne__", [](const TaintRangePtr& self, const TaintRangePtr& other) {
          if (other == nullptr)
              return true;
          return self->start != other->start || self->length != other->length;
      });
}
