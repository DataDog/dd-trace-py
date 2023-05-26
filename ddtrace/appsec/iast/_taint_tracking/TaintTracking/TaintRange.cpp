#include "Initializer/Initializer.h"
#include "TaintRange.h"
#include "Utils/StringUtils.h"

#include <sstream>

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

size_t TaintRange::get_hash() const {
  size_t hstart = hash<size_t>()(this->start);
  size_t hlength = hash<size_t>()(this->length);
  size_t hsource = hash<size_t>()(this->source->get_hash());
  return hstart ^ hlength ^ hsource;
};

TaintRangePtr shift_taint_range(const TaintRangePtr& source_taint_range, int offset) {
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset,  // start
                                                  source_taint_range->length, // length
                                                  source_taint_range->source); // origin
    return tptr;
}

TaintRangeRefs shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, long offset) {
    TaintRangeRefs new_ranges;
    new_ranges.reserve(source_taint_ranges.size());

    for (const auto& trange : source_taint_ranges) {
        new_ranges.emplace_back(shift_taint_range(trange, offset));
    }
    return new_ranges;
}

static TaintRangeRefs get_ranges_for_string(const PyObject* str, TaintRangeMapType* tx_taint_map) {
    if (not tx_taint_map or tx_taint_map->empty()) {
        return {};
    }

    const auto it = tx_taint_map->find(get_unique_id(str));
    if (it == tx_taint_map->end()) {
        return {};
    }

    return it->second->get_ranges();
}

TaintRangeRefs get_ranges_impl(const PyObject* string_input, TaintRangeMapType* tx_map) {
    if (not tx_map) {
        tx_map = initializer->get_tainting_map();
    }
    return get_ranges_for_string(string_input, tx_map);
}

void set_ranges_impl(const PyObject* str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map) {
    if (not tx_map or ranges.empty()) {
        return;
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
void set_ranges_impl(const PyObject* str, const TaintRangeRefs& ranges) {
    set_ranges_impl(str, ranges, nullptr);
}

// Returns a tuple with (all ranges, ranges of candidate_text)
std::tuple<TaintRangeRefs, TaintRangeRefs> are_all_text_all_ranges(const PyObject* candidate_text,
                                                                   const py::tuple& parameter_list) {
    // TODO: pass tx_map to the function (currently not used in the benchmark)
    auto tx_map = initializer->get_tainting_map();
    TaintRangeRefs candidate_text_ranges{get_ranges_impl(candidate_text, tx_map)};
    TaintRangeRefs all_ranges;

    for (const auto& param_handler : parameter_list) {
        auto param = param_handler.cast<py::object>().ptr();

        if (is_text(param)) {
            // TODO: OPT
            TaintRangeRefs ranges{get_ranges_impl(param, tx_map)};
            all_ranges.insert(all_ranges.end(), ranges.begin(), ranges.end());
        }
    }

    all_ranges.insert(all_ranges.end(), candidate_text_ranges.begin(), candidate_text_ranges.end());
    return {all_ranges, candidate_text_ranges};
}

TaintRangeRefs is_some_text_and_get_ranges(PyObject* candidate_text, TaintRangeMapType* tx_map) {
    if (!is_text(candidate_text)) {
        return {};
    }
    return get_ranges_impl(candidate_text, tx_map);
}

TaintRangeRefs is_some_text_and_get_ranges(PyObject* candidate_text) {
    auto tx_map = initializer->get_tainting_map();
    return is_some_text_and_get_ranges(candidate_text, tx_map);
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

TaintedObject* get_tainted_object(const PyObject* str, TaintRangeMapType* tx_taint_map) {
    if (!could_be_tainted(str) or !tx_taint_map or tx_taint_map->empty()) {
        return nullptr;
    }

    auto it = tx_taint_map->find(get_unique_id(str));
    return it == tx_taint_map->end() ? nullptr : it->second;
}

void set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map) {
    if (not tx_taint_map)
        return;

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

    // TODO: check return value policy
    m.def("get_tainted_object", &get_tainted_object, "str"_a, "tx_taint_map"_a);
    m.def(
            "is_some_text_and_get_ranges", [](PyObject* s) { return is_some_text_and_get_ranges(s); },
            py::return_value_policy::move);

    m.def("shift_taint_range", &shift_taint_range, py::return_value_policy::move, "source_taint_range"_a, "offset"_a);

    // FIXME: no need to be a lambda now
    m.def("set_ranges", [](const PyObject* str, const TaintRangeRefs& ranges) {
        return set_ranges_impl(str, ranges);
    });

    m.def("get_ranges", [](const PyObject* s) {
        return get_ranges_impl(s, nullptr);
    }, "string_input"_a, py::return_value_policy::take_ownership);

    m.def("get_range_by_hash", &get_range_by_hash, "range_hash"_a, "taint_ranges"_a);

    py::class_<TaintRange, shared_ptr<TaintRange>>(m, "TaintRange")
            .def(py::init<int, int, Source>(), "start"_a = "", "length"_a, "source"_a)
            .def_readonly("start", &TaintRange::start)
            .def_readonly("length", &TaintRange::length)
            .def_readonly("source", &TaintRange::source)
            .def("__str__", &TaintRange::toString)
            .def("__repr__", &TaintRange::toString)
            .def("__hash__", &TaintRange::hash_)
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
