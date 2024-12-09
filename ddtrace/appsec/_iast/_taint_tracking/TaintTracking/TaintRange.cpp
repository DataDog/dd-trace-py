#include "TaintRange.h"
#include "Initializer/Initializer.h"
#include "Utils/StringUtils.h"

namespace py = pybind11;

void
TaintRange::reset()
{
    source.reset();
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

    // TODO: check all the py::return_value_policy

    // m.def("set_ranges", py::overload_cast<PyObject*, const TaintRangeRefs&>(&api_set_ranges), "str"_a, "ranges"_a);
    m.def("set_ranges", &api_set_ranges, "str"_a, "ranges"_a);
    m.def("get_ranges", &api_get_ranges, "string_input"_a, py::return_value_policy::take_ownership);

    // Fake constructor, used to force calling allocate_taint_range for performance reasons
    m.def(
      "taint_range",
      [](const RANGE_START start, const RANGE_LENGTH length, const Source& source) {
          return initializer->allocate_taint_range(start, length, source);
      },
      "start"_a,
      "length"_a,
      "source"_a,
      py::return_value_policy::move);

    py::class_<TaintRange, shared_ptr<TaintRange>>(m, "TaintRange_")
      // Normal constructor disabled on the Python side, see above
      .def_readonly("start", &TaintRange::start)
      .def_readonly("length", &TaintRange::length)
      .def_readonly("source", &TaintRange::source)
      .def("__str__", &TaintRange::toString)
      .def("__repr__", &TaintRange::toString)
      .def("__hash__", &TaintRange::get_hash)
      .def("get_hash", &TaintRange::get_hash)
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
