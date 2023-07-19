#pragma once
#include <iostream>
#include <sstream>
#include <utility>

#include <pybind11/stl.h>

#include "absl/container/node_hash_map.h"
#include "structmember.h"

#include "Constants.h"
#include "TaintTracking/Source.h"
#include "Utils/StringUtils.h"

#define PY_MODULE_NAME_TAINTRANGES                                                                                     \
    PY_MODULE_NAME "."                                                                                                 \
                   "TaintRange"

using namespace std;
namespace py = pybind11;

// Forward declarations
class TaintedObject;

// Alias
using TaintedObjectPtr = TaintedObject*;
using TaintRangeMapType = absl::node_hash_map<uintptr_t, TaintedObjectPtr>;

inline static uintptr_t
get_unique_id(const PyObject* str)
{
    return uintptr_t(str);
}

struct TaintRange
{
    int start = 0;
    int length = 0;
    SourcePtr source = nullptr;

    TaintRange() = default;

    TaintRange(int start, int length, SourcePtr source)
      : start(start)
      , length(length)
      , source(source)
    {}

    TaintRange(int start, int length, const Source& source);

    inline void set_values(int start_, int length_, SourcePtr source_)
    {
        start = start_;
        length = length_;
        source = source_;
    }

    void reset();

    [[nodiscard]] string toString() const;

    [[nodiscard]] uint get_hash() const;

    // FIXME: unneeded?
    //    struct hash_fn {
    //        size_t operator()(const TaintRange &range) const { return
    //        range.get_hash(); }
    //    };
    //
    //    [[nodiscard]] size_t hash_() const { return hash_fn()(*this); }
    //
    explicit operator std::string() const;
};

using TaintRangePtr = shared_ptr<TaintRange>;
using TaintRangeRefs = vector<TaintRangePtr>;

TaintRangePtr
api_shift_taint_range(const TaintRangePtr& source_taint_range, int offset);

TaintRangeRefs
api_shift_taint_ranges(const TaintRangeRefs&, long offset);

TaintRangeRefs
get_ranges(const PyObject* string_input, TaintRangeMapType* tx_map);
inline TaintRangeRefs
get_ranges(const PyObject* string_input)
{
    return get_ranges(string_input, nullptr);
}
inline TaintRangeRefs
api_get_ranges(const py::object string_input)
{
    return get_ranges(string_input.ptr());
}

void
set_ranges(const PyObject* str, const TaintRangeRefs& ranges, TaintRangeMapType* tx_map);

inline void
set_ranges(const PyObject* str, const TaintRangeRefs& ranges)
{
    set_ranges(str, ranges, nullptr);
}
inline void
api_set_ranges(const py::object str, const TaintRangeRefs& ranges)
{
    set_ranges(str.ptr(), ranges);
}

// Returns a tuple with (all ranges, ranges of candidate_text)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(const PyObject* candidate_text, const py::tuple& parameter_list);
inline std::tuple<TaintRangeRefs, TaintRangeRefs>
api_are_all_text_all_ranges(const py::object candidate_text, const py::tuple& parameter_list)
{
    return are_all_text_all_ranges(candidate_text.ptr(), parameter_list);
}

TaintRangePtr
get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges);

void
set_fast_tainted_if_notinterned_unicode(const PyObject* objptr);
inline void
api_set_fast_tainted_if_unicode(const py::object obj)
{
    set_fast_tainted_if_notinterned_unicode(obj.ptr());
}

bool
is_notinterned_notfasttainted_unicode(const PyObject* objptr);
inline bool
api_is_unicode_and_not_fast_tainted(const py::object str)
{
    return is_notinterned_notfasttainted_unicode(str.ptr());
}

TaintedObject*
get_tainted_object(const PyObject* str, TaintRangeMapType* tx_taint_map);

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, TaintRangeMapType* tx_taint_map);

void
pyexport_taintrange(py::module& m);
