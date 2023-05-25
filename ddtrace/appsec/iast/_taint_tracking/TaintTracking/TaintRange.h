#pragma once
#include <iostream>
#include <sstream>
#include <utility>

#include <pybind11/stl.h>

#include "absl/container/node_hash_map.h"
#include "structmember.h"

#include "Constants.h"
#include "TaintTracking/Source.h"

#define PY_MODULE_NAME_TAINTRANGES PY_MODULE_NAME "." "TaintRange"

using namespace std;
namespace py = pybind11;

// Forward declarations
class TaintedObject;

// Alias
using TaintedObjectPtr = TaintedObject*;
using TaintRangeMapType = absl::node_hash_map<uintptr_t, TaintedObjectPtr>;

template <class StrType>
inline static uintptr_t get_unique_id(const StrType str) {
    PyObject* pyo = py::cast<py::object>(str).ptr();
    return uintptr_t(pyo);
}

struct TaintRange {
    int start = 0;
    int length = 0;
    SourcePtr source = nullptr;

    TaintRange() = default;

    TaintRange(int start, int length, SourcePtr source)
            : start(start),
              length(length),
              source(source){}

    TaintRange(int start, int length, const Source& source);

    inline void set_values(int start_, int length_, SourcePtr source_) {
        start = start_;
        length = length_;
        source = source_;
    }


    void reset();

    [[nodiscard]] string toString() const;

    [[nodiscard]] size_t get_hash() const;

    struct hash_fn {
        size_t operator()(const TaintRange &range) const { return range.get_hash(); }
    };

    [[nodiscard]] size_t hash_() const { return hash_fn()(*this); }

    explicit operator std::string() const;
};

using TaintRangePtr = shared_ptr<TaintRange>;
using TaintRangeRefs = vector<TaintRangePtr>;

inline auto operator<(const TaintRange& left, const TaintRange& right) {
    return left.start < right.start;
}

TaintRangePtr shift_taint_range(const TaintRangePtr& source_taint_range, int offset);

TaintRangeRefs shift_taint_ranges(const TaintRangeRefs&, long offset);

template <class TaintableType>
TaintRangeRefs get_ranges_impl(const TaintableType& string_input, TaintRangeMapType* tx_map=nullptr);

template <class StrType>
void set_ranges_impl(const StrType& str, const TaintRangeRefs& ranges);
template <class StrType>
void set_ranges_impl(const StrType& str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map);

TaintRangeRefs get_ranges_dispatcher(const py::object& string_input, TaintRangeMapType* tx_map);

// Returns a tuple with (all ranges, ranges of candidate_text)
template <class StrType>
std::tuple<TaintRangeRefs, TaintRangeRefs> are_all_text_all_ranges(const StrType& candidate_text,
                                                                   const py::tuple& parameter_list);

template <class StrType>
TaintRangeRefs is_some_text_and_get_ranges(const StrType& candidate_text, TaintRangeMapType* tx_map);
template <class StrType>
TaintRangeRefs is_some_text_and_get_ranges(const StrType& candidate_text);

TaintRangePtr get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges);

template <class StrType>
bool could_be_tainted(const StrType);

template <class StrType>
void set_could_be_tainted(StrType);

template <class StrType>
TaintedObject* get_tainted_object(const StrType str, TaintRangeMapType* tx_taint_map);

template <class StrType>
void set_tainted_object(StrType str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map);

void pyexport_taintrange(py::module& m);
