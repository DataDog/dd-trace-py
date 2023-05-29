#include "Initializer/Initializer.h"
#include "TaintRange.h"
#include "Utils/StringUtils.h"

#include <sstream>
#include <iostream> // JJJ remove

using namespace pybind11::literals;

using namespace std;

// Only for Python! C++ should use the constructor with the TaintOriginPtr defined in the header
TaintRange::TaintRange(int start, int length, const Source& source)
        : start(start),
          length(length) {
    this->source = initializer->allocate_taint_source(source.name, source.value, source.origin);
}
void TaintRange::reset() {
    if (source) {
        initializer->release_taint_source(source);
        source = nullptr;
    }

    start = 0;
    length = 0;
};

string TaintRange::toString() const {
    ostringstream ret;
    ret << "TaintRange at " << this << " "
      << "[start=" << start << ", length=" << length
      << " source=" << source->toString() << "]";
    return ret.str();
}

TaintRange::operator std::string() const { return toString(); }

// Note: don't use size_t or long, if the hash is bigger than an int, Python will re-hash it!
uint TaintRange::get_hash() const {
  uint hstart = hash<uint>()(this->start);
  uint hlength = hash<uint>()(this->length);
  uint hsource = hash<uint>()(this->source->get_hash());
  return hstart ^ hlength ^ hsource;
};

TaintRangePtr api_shift_taint_range(const TaintRangePtr& source_taint_range, int offset) {
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset,  // start
                                                  source_taint_range->length, // length
                                                  source_taint_range->source); // origin
    return tptr;
}

TaintRangeRefs api_shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, long offset) {
    TaintRangeRefs new_ranges;
    new_ranges.reserve(source_taint_ranges.size());

    for (const auto& trange : source_taint_ranges) {
        new_ranges.emplace_back(api_shift_taint_range(trange, offset));
    }
    return new_ranges;
}


// FIXME: add check that str is really some kind of string
static TaintRangeRefs get_ranges_for_string(const PyObject* str, TaintRangeMapType* tx_map) {
    const auto it = tx_map->find(get_unique_id(str));
    if (it == tx_map->end()) {
        return {};
    }

    return it->second->get_ranges();
}

TaintRangeRefs get_ranges(const PyObject* string_input, TaintRangeMapType* tx_map) {
    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
    }
    if (tx_map->empty()) {
        return {};
    }
    return get_ranges_for_string(string_input, tx_map);
}


// FIXME: add check that str is really some kind of string
void set_ranges(const PyObject* str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map) {
    if (ranges.empty())
        return;

    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
    }

    auto tx_id = initializer->context_id();
    if (tx_id == 0) {
        return;
    }

    auto hash = get_unique_id(str);
    auto it = tx_map->find(hash);
    auto new_tainted_object = initializer->allocate_tainted_object(ranges);
    set_could_be_tainted(str);
    new_tainted_object->incref();
    if (it != tx_map->end()) {
        it->second->decref();
        it->second = new_tainted_object;
        return;
    }

    tx_map->insert({hash, new_tainted_object});
}

// Returns a tuple with (all ranges, ranges of candidate_text)
// FIXME: add check that candidate_text is really some kind of string
// FIXME: Take a PyList as parameter_list instead of a py::tuple
std::tuple<TaintRangeRefs, TaintRangeRefs> are_all_text_all_ranges(const PyObject* candidate_text,
                                                                   const py::tuple& parameter_list) {
    // TODO: pass tx_map to the function
    auto tx_map = initializer->get_tainting_map();
    TaintRangeRefs candidate_text_ranges{get_ranges(candidate_text, tx_map)};
    TaintRangeRefs all_ranges;

    for (const auto& param_handler : parameter_list) {
        auto param = param_handler.cast<py::object>().ptr();

        if (is_text(param)) {
            // TODO: OPT
            TaintRangeRefs ranges{get_ranges(param, tx_map)};
            all_ranges.insert(all_ranges.end(), ranges.begin(), ranges.end());
        }
    }

    all_ranges.insert(all_ranges.end(), candidate_text_ranges.begin(), candidate_text_ranges.end());
    return {all_ranges, candidate_text_ranges};
}

TaintRangePtr get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges) {
    if (!taint_ranges or taint_ranges->empty()) {
        return nullptr;
    }
    // TODO: Replace this loop with a efficient function, vector.find() is O(n) too.
    TaintRangePtr null_range = nullptr;
    for (const auto& range : taint_ranges.value()) {
        if (range_hash == range->get_hash()) {
            return range;
        }
    }
    return null_range;
}

typedef struct _PyASCIIObject_State_Hidden {
    unsigned int : 8;
    unsigned int hidden : 24;
} PyASCIIObject_State_Hidden;

__attribute__((flatten)) bool could_be_tainted(const PyObject* objptr) {
    if (objptr == nullptr) {
        return false;
    }
    if (!PyUnicode_Check(objptr)) {
        return true;
    }
    if (PyUnicode_CHECK_INTERNED(objptr) != SSTATE_NOT_INTERNED) {
        return false;
    }
    const PyASCIIObject_State_Hidden* e = (PyASCIIObject_State_Hidden*) &(((PyASCIIObject*)objptr)->state);
    return e->hidden == 1;
}

__attribute__((flatten)) void set_could_be_tainted(const PyObject* objptr) {
    if (objptr == nullptr) {
        return;
    }
    if (!PyUnicode_Check(objptr)) {
        return;
    }
    if (PyUnicode_CHECK_INTERNED(objptr) != SSTATE_NOT_INTERNED) {
        return;
    }
    auto e = (PyASCIIObject_State_Hidden*) &(((PyASCIIObject*) objptr)->state);
    e->hidden = 1;
}

TaintedObject* get_tainted_object(const PyObject* str, TaintRangeMapType* tx_map) {
    if (!tx_map) {
        tx_map = initializer->get_tainting_map();
    }
    if (!could_be_tainted(str) or tx_map->empty()) {
        return nullptr;
    }

    auto it = tx_map->find(get_unique_id(str));
    return it == tx_map->end() ? nullptr : it->second;
}

void set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map) {
    if (not tx_taint_map) {
        tx_taint_map = initializer->get_tainting_map();
    }

    auto hash = get_unique_id(str);
    auto it = tx_taint_map->find(hash);
    set_could_be_tainted(str);
    if (it != tx_taint_map->end()) {
        // The same memory address was probably re-used for a different PyObject, so we need to overwrite it.
        if (it->second != tainted_object) {
            // If the tainted object is different, we need to decref the previous one and incref the new one.
            // But if it's the same object, we can avoid both operations, since they would be redundant.
            it->second->decref();
            tainted_object->incref();
            it->second = tainted_object;
        }
        return;
    }
    tainted_object->incref();
    tx_taint_map->insert({hash, tainted_object});
}

void pyexport_taintrange(py::module& m) {
    // TODO OPT: check all the py::return_value_policy
    m.def("are_all_text_all_ranges", &are_all_text_all_ranges, "candidate_text"_a, "parameter_list"_a,
          py::return_value_policy::move);
    m.def("are_all_text_all_ranges", &api_are_all_text_all_ranges, "candidate_text"_a, "parameter_list"_a,
          py::return_value_policy::move);

    // TODO: check return value policy
    m.def("get_tainted_object", &get_tainted_object, "str"_a, "tx_taint_map"_a);

    m.def("shift_taint_range", &api_shift_taint_range, py::return_value_policy::move,
          "source_taint_range"_a, "offset"_a);
    m.def("shift_taint_ranges", &api_shift_taint_ranges, py::return_value_policy::move,
          "ranges"_a, "offset"_a);

    m.def("set_ranges", py::overload_cast<const PyObject*, const TaintRangeRefs&>(&set_ranges), "str"_a, "ranges"_a);
    m.def("set_ranges", &api_set_ranges, "str"_a, "ranges"_a);

    m.def("get_ranges", py::overload_cast<const PyObject*>(&get_ranges), "string_input"_a,
          py::return_value_policy::take_ownership);
    m.def("get_ranges", &api_get_ranges, "string_input"_a,
          py::return_value_policy::take_ownership);

    m.def("get_range_by_hash", &get_range_by_hash, "range_hash"_a, "taint_ranges"_a);

    py::class_<TaintRange, shared_ptr<TaintRange>>(m, "TaintRange")
            .def(py::init<int, int, Source>(), "start"_a = "", "length"_a, "source"_a)
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
