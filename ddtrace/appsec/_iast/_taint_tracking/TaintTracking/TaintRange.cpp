#include "TaintRange.h"
#include "Initializer/Initializer.h"
#include "Utils/StringUtils.h"

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
    uint hstart = hash<uint>()(this->start);
    uint hlength = hash<uint>()(this->length);
    uint hsource = hash<uint>()(this->source.get_hash());
    return hstart ^ hlength ^ hsource;
};

TaintRangePtr
api_shift_taint_range(const TaintRangePtr& source_taint_range, RANGE_START offset)
{
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset, // start
                                                  source_taint_range->length,         // length
                                                  source_taint_range->source);        // origin
    return tptr;
}

TaintRangeRefs
shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, RANGE_START offset)
{
    TaintRangeRefs new_ranges;
    new_ranges.reserve(source_taint_ranges.size());

    for (const auto& trange : source_taint_ranges) {
        new_ranges.emplace_back(api_shift_taint_range(trange, offset));
    }
    return new_ranges;
}

TaintRangeRefs
api_shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, RANGE_START offset)
{
    return shift_taint_ranges(source_taint_ranges, offset);
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
 *   @param args[1] long. Lenght of the string
 *   @param args[2] string. source name
 *   @param args[3] string. source value
 *   @param args[4] int. origin type
 * @param nargs The number of arguments in the 'args' array.
 */
PyObject*
api_set_ranges_from_values(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{

    if (nargs != 5) {
        PyErr_SetString(
          PyExc_TypeError,
          "Invalid number of params: pyobject_newid, len(pyobject), source_name, source_value, source_origin");
        throw std::runtime_error(
          "Invalid number of params: pyobject_newid, len(pyobject), source_name, source_value, source_origin");
    }
    PyObject* tainted_object = args[0];
    if (!PyUnicode_Check(tainted_object) and !PyByteArray_Check(tainted_object) and !PyBytes_Check(tainted_object)) {
        return tainted_object;
    }
    auto ctx_map = initializer->get_tainting_map();
    PyObject* pyobject_n = new_pyobject_id(tainted_object);
    PyObject* len_pyobject_py = args[1];

    long len_pyobject = PyLong_AsLong(len_pyobject_py);
    string source_name = PyObjectToString(args[2]);
    string source_value = PyObjectToString(args[3]);
    auto source_origin = OriginType(PyLong_AsLong(args[4]));
    auto source = Source(source_name, source_value, source_origin);
    auto range = initializer->allocate_taint_range(0, len_pyobject, source);
    TaintRangeRefs ranges = vector{ range };
    set_ranges(pyobject_n, ranges, ctx_map);
    return pyobject_n;
}

TaintRangeRefs
get_ranges(PyObject* string_input, TaintRangeMapType* tx_map)
{
    if (not is_text(string_input))
        return {};

    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
    }
    if (!tx_map or tx_map->empty()) {
        // TODO: log something here: "no tx_map, maybe call create_context()?"
        return {};
    }

    const auto it = tx_map->find(get_unique_id(string_input));
    if (it == tx_map->end()) {
        return {};
    }

    if (get_internal_hash(string_input) != it->second.first) {
        tx_map->erase(it);
        return {};
    }

    return it->second.second->get_ranges();
}

void
set_ranges(PyObject* str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map)
{
    if (not is_text(str) or ranges.empty())
        return;

    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
        if (not tx_map) {
            throw py::value_error("Tainted Map isn't initialized. Call create_context() first");
        }
    }

    auto tx_id = initializer->context_id();
    if (tx_id == 0) {
        return;
    }

    auto obj_id = get_unique_id(str);
    auto it = tx_map->find(obj_id);
    auto new_tainted_object = initializer->allocate_ranges_into_taint_object(ranges);

    set_fast_tainted_if_notinterned_unicode(str);
    new_tainted_object->incref();
    if (it != tx_map->end()) {
        it->second.second->decref();
        it->second = std::make_pair(get_internal_hash(str), new_tainted_object);
        return;
    }

    tx_map->insert({ obj_id, std::make_pair(get_internal_hash(str), new_tainted_object) });
}

// Returns a tuple with (all ranges, ranges of candidate_text)
// FIXME: add check that candidate_text is really some kind of string
// FIXME: Take a PyList as parameter_list instead of a py::tuple (same for the
// result)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(PyObject* candidate_text, const py::tuple& parameter_list)
{
    if (not is_text(candidate_text))
        return {};
    // TODO: pass tx_map to the function
    auto tx_map = initializer->get_tainting_map();
    TaintRangeRefs candidate_text_ranges{ get_ranges(candidate_text, tx_map) };
    TaintRangeRefs all_ranges;

    for (const auto& param_handler : parameter_list) {
        auto param = param_handler.cast<py::object>().ptr();

        if (is_text(param)) {
            // TODO: OPT
            TaintRangeRefs ranges{ get_ranges(param, tx_map) };
            all_ranges.insert(all_ranges.end(), ranges.begin(), ranges.end());
        }
    }

    all_ranges.insert(all_ranges.end(), candidate_text_ranges.begin(), candidate_text_ranges.end());
    return { all_ranges, candidate_text_ranges };
}

TaintRangePtr
get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges)
{
    if (!taint_ranges or taint_ranges->empty()) {
        return nullptr;
    }
    // TODO: Replace this loop with a efficient function, vector.find() is O(n)
    // too.
    TaintRangePtr null_range = nullptr;
    for (const auto& range : taint_ranges.value()) {
        if (range_hash == range->get_hash()) {
            return range;
        }
    }
    return null_range;
}

inline void
api_copy_ranges_from_strings(py::object& str_1, py::object& str_2)
{
    auto tx_map = initializer->get_tainting_map();
    auto ranges = get_ranges(str_1.ptr(), tx_map);
    set_ranges(str_2.ptr(), ranges, tx_map);
}

inline void
api_copy_and_shift_ranges_from_strings(py::object& str_1, py::object& str_2, int offset)
{
    auto tx_map = initializer->get_tainting_map();
    auto ranges = get_ranges(str_1.ptr(), tx_map);
    set_ranges(str_2.ptr(), shift_taint_ranges(ranges, offset), tx_map);
}

TaintedObjectPtr
get_tainted_object(PyObject* str, TaintRangeMapType* tx_map)
{
    if (not str)
        return nullptr;

    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
        if (not tx_map) {
            throw py::value_error("Tainted Map isn't initialized. Call create_context() first");
        }
    }
    if (is_notinterned_notfasttainted_unicode(str) or tx_map->empty()) {
        return nullptr;
    }

    auto it = tx_map->find(get_unique_id(str));
    if (it == tx_map->end()) {
        return nullptr;
    }

    if (get_internal_hash(str) != it->second.first) {
        it->second.second->decref();
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
    if (PyByteArray_Check(obj)) {
        return bytearray_hash(obj);
    }

    return PyObject_Hash(obj);
}

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map)
{
    if (not str or not is_text(str)) {
        return;
    }

    if (not tx_taint_map) {
        tx_taint_map = initializer->get_tainting_map();
        if (not tx_taint_map) {
            throw py::value_error("Tainted Map isn't initialized. Call create_context() first");
        }
    }

    auto obj_id = get_unique_id(str);
    set_fast_tainted_if_notinterned_unicode(str);
    auto it = tx_taint_map->find(obj_id);
    if (it != tx_taint_map->end()) {
        // The same memory address was probably re-used for a different PyObject, so
        // we need to overwrite it.
        if (it->second.second != tainted_object) {
            // If the tainted object is different, we need to decref the previous one
            // and incref the new one. But if it's the same object, we can avoid both
            // operations, since they would be redundant.
            it->second.second->decref();
            tainted_object->incref();
            it->second = std::make_pair(get_internal_hash(str), tainted_object);
        }
        // Update the hash, because for bytearrays it could have changed after the extend operation
        it->second.first = get_internal_hash(str);
        return;
    }
    tainted_object->incref();
    tx_taint_map->insert({ obj_id, std::make_pair(get_internal_hash(str), tainted_object) });
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
    m.def("are_all_text_all_ranges",
          &are_all_text_all_ranges,
          "candidate_text"_a,
          "parameter_list"_a,
          py::return_value_policy::move);
    m.def("are_all_text_all_ranges",
          &api_are_all_text_all_ranges,
          "candidate_text"_a,
          "parameter_list"_a,
          py::return_value_policy::move);

    // TODO: check return value policy
    m.def("get_tainted_object", &get_tainted_object, "str"_a, "tx_taint_map"_a);

    m.def(
      "shift_taint_range", &api_shift_taint_range, py::return_value_policy::move, "source_taint_range"_a, "offset"_a);
    m.def("shift_taint_ranges", &api_shift_taint_ranges, py::return_value_policy::move, "ranges"_a, "offset"_a);

    m.def("set_ranges", py::overload_cast<PyObject*, const TaintRangeRefs&>(&set_ranges), "str"_a, "ranges"_a);
    m.def("set_ranges", &api_set_ranges, "str"_a, "ranges"_a);

    m.def("copy_ranges_from_strings", &api_copy_ranges_from_strings, "str_1"_a, "str_2"_a);
    m.def(
      "copy_and_shift_ranges_from_strings", &api_copy_and_shift_ranges_from_strings, "str_1"_a, "str_2"_a, "offset"_a);

    m.def("get_ranges",
          py::overload_cast<PyObject*>(&get_ranges),
          "string_input"_a,
          py::return_value_policy::take_ownership);
    m.def("get_ranges", &api_get_ranges, "string_input"_a, py::return_value_policy::take_ownership);

    m.def("get_range_by_hash", &get_range_by_hash, "range_hash"_a, "taint_ranges"_a);

    // Fake constructor, used to force calling allocate_taint_range for performance reasons
    m.def(
      "taint_range",
      [](RANGE_START start, RANGE_LENGTH length, Source source) {
          return initializer->allocate_taint_range(start, length, std::move(source));
      },
      "start"_a,
      "length"_a,
      "source"_a);

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
