#include "TaintRange.h"
#include "Initializer/Initializer.h"

#include <utility>

using namespace pybind11::literals;

using namespace std;

#define _GET_HASH_KEY(obj) ((((PyASCIIObject*)obj)->hash) & 0xFFFFFF)

PyObject* HASH_FUNC = PyDict_GetItemString(PyEval_GetBuiltins(), "hash");

typedef struct _PyASCIIObject_State_Hidden
{
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

// Used to quickly exit on cases where the object is a non interned unicode
// string and does not have the fast-taint mark on its internal data structure.
// In any other case it will return false so the evaluation continue for (more
// slowly) checking if bytes and bytearrays are tainted.
__attribute__((flatten)) bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr)
{
    if (!objptr) {
        return true; // cannot taint a nullptr
    }

    if (!PyUnicode_Check(objptr)) {
        return false; // not a unicode, continue evaluation
    }

    if (PyUnicode_CHECK_INTERNED(objptr)) {
        return true; // interned but it could still be tainted
    }

    const PyASCIIObject_State_Hidden* e = (PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)objptr)->state);
    if (!e) {
        return true; // broken string object? better to skip it
    }
    // it cannot be fast tainted if hash is set to -1 (not computed)
    return (((PyASCIIObject*)objptr)->hash) == -1 || e->hidden != _GET_HASH_KEY(objptr);
}

// For non interned unicode strings, set a hidden mark on it's internsal data
// structure that will allow us to quickly check if the string is not tainted
// and thus skip further processing without having to search on the tainting map
__attribute__((flatten)) void
set_fast_tainted_if_notinterned_unicode(const PyObject* objptr)
{
    if (not objptr or !PyUnicode_Check(objptr) or PyUnicode_CHECK_INTERNED(objptr)) {
        return;
    }
    auto e = (PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)objptr)->state);
    if (e) {
        if ((((PyASCIIObject*)objptr)->hash) == -1) {
            PyObject* result = PyObject_CallFunctionObjArgs(HASH_FUNC, objptr, NULL);
            if (result != NULL) {
                Py_DECREF(result);
            }
        }
        e->hidden = _GET_HASH_KEY(objptr);
    }
}

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
api_shift_taint_range(const TaintRangePtr& source_taint_range, int offset)
{
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset, // start
                                                  source_taint_range->length,         // length
                                                  source_taint_range->source);        // origin
    return tptr;
}

TaintRangeRefs
api_shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, long offset)
{
    TaintRangeRefs new_ranges;
    new_ranges.reserve(source_taint_ranges.size());

    for (const auto& trange : source_taint_ranges) {
        new_ranges.emplace_back(api_shift_taint_range(trange, offset));
    }
    return new_ranges;
}

TaintRangeRefs
get_ranges(const PyObject* string_input, TaintRangeMapType* tx_map)
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

    return it->second->get_ranges();
}

void
set_ranges(const PyObject* str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map)
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

    auto hash = get_unique_id(str);
    auto it = tx_map->find(hash);
    auto new_tainted_object = initializer->allocate_tainted_object(ranges);
    set_fast_tainted_if_notinterned_unicode(str);
    new_tainted_object->incref();
    if (it != tx_map->end()) {
        it->second->decref();
        it->second = new_tainted_object;
        return;
    }

    tx_map->insert({ hash, new_tainted_object });
}

// Returns a tuple with (all ranges, ranges of candidate_text)
// FIXME: add check that candidate_text is really some kind of string
// FIXME: Take a PyList as parameter_list instead of a py::tuple (same for the
// result)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(const PyObject* candidate_text, const py::tuple& parameter_list)
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

TaintedObjectPtr
get_tainted_object(const PyObject* str, TaintRangeMapType* tx_map)
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
    return it == tx_map->end() ? nullptr : it->second;
}

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map)
{
    if (not str or not is_text(str))
        return;

    if (not tx_taint_map) {
        tx_taint_map = initializer->get_tainting_map();
        if (not tx_taint_map) {
            throw py::value_error("Tainted Map isn't initialized. Call create_context() first");
        }
    }

    auto hash = get_unique_id(str);
    auto it = tx_taint_map->find(hash);
    set_fast_tainted_if_notinterned_unicode(str);
    if (it != tx_taint_map->end()) {
        // The same memory address was probably re-used for a different PyObject, so
        // we need to overwrite it.
        if (it->second != tainted_object) {
            // If the tainted object is different, we need to decref the previous one
            // and incref the new one. But if it's the same object, we can avoid both
            // operations, since they would be redundant.
            it->second->decref();
            tainted_object->incref();
            it->second = tainted_object;
        }
        return;
    }
    tainted_object->incref();
    tx_taint_map->insert({ hash, tainted_object });
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
          py::overload_cast<const PyObject*>(&set_fast_tainted_if_notinterned_unicode),
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

    m.def("set_ranges", py::overload_cast<const PyObject*, const TaintRangeRefs&>(&set_ranges), "str"_a, "ranges"_a);
    m.def("set_ranges", &api_set_ranges, "str"_a, "ranges"_a);

    m.def("get_ranges",
          py::overload_cast<const PyObject*>(&get_ranges),
          "string_input"_a,
          py::return_value_policy::take_ownership);
    m.def("get_ranges", &api_get_ranges, "string_input"_a, py::return_value_policy::take_ownership);

    m.def("get_range_by_hash", &get_range_by_hash, "range_hash"_a, "taint_ranges"_a);

    // Fake constructor, used to force calling allocate_taint_range for performance reasons
    m.def(
      "taint_range",
      [](int start, int length, Source source) {
          return initializer->allocate_taint_range(start, length, std::move(source));
      },
      "start"_a,
      "length"_a,
      "source"_a);

    py::class_<TaintRange, shared_ptr<TaintRange>>(m, "TaintRange_")
      // Normal constructor disabled on the Python side, see above
      // .def(py::init<int, int, Source>(), "start"_a = "", "length"_a, "source"_a)
      .def_readonly("start", &TaintRange::start)
      .def_readonly("length", &TaintRange::length)
      .def_readonly("source", &TaintRange::source)
      .def("__str__", &TaintRange::toString)
      .def("__repr__", &TaintRange::toString)
      .def("__hash__", &TaintRange::get_hash)
      .def("get_hash", &TaintRange::get_hash)
      // FIXME: check source to for these two?
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
