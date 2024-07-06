#pragma once
#include <utility>

#include <pybind11/stl.h>

#include "structmember.h"

#include "Constants.h"
#include "TaintTracking/Source.h"
#include "Utils/StringUtils.h"

using namespace std;
namespace py = pybind11;

// Forward declarations
class TaintedObject;

// Alias
using TaintedObjectPtr = TaintedObject*;

#ifdef NDEBUG // Decide wether to use abseil

#include "absl/container/node_hash_map.h"
using TaintRangeMapType = absl::node_hash_map<uintptr_t, std::pair<Py_hash_t, TaintedObjectPtr>>;

#else

#include <unordered_map>
using TaintRangeMapType = std::map<uintptr_t, std::pair<Py_hash_t, TaintedObjectPtr>>;

#endif // NDEBUG

using TaintRangeMapTypePtr = shared_ptr<TaintRangeMapType>;
// using TaintRangeMapTypePtr = TaintRangeMapType*;

struct TaintRange
{
    RANGE_START start = 0;
    RANGE_LENGTH length = 0;
    Source source;

    TaintRange() = default;

    TaintRange(const RANGE_START start, const RANGE_LENGTH length, Source source)
      : start(start)
      , length(length)
      , source(std::move(source))
    {
        if (length <= 0) {
            throw std::invalid_argument("Error: Length cannot be set to 0.");
        }
    }

    inline void set_values(const RANGE_START start_, const RANGE_LENGTH length_, Source source_)
    {
        if (length_ <= 0) {
            throw std::invalid_argument("Error: Length cannot be set to 0.");
        }
        start = start_;
        length = length_;
        source = std::move(source_);
    }

    void reset();

    [[nodiscard]] string toString() const;

    [[nodiscard]] uint get_hash() const;

    explicit operator std::string() const;
};

using TaintRangePtr = shared_ptr<TaintRange>;
using TaintRangeRefs = vector<TaintRangePtr>;

TaintRangePtr
shift_taint_range(const TaintRangePtr& source_taint_range, RANGE_START offset, RANGE_LENGTH new_length);

inline TaintRangePtr
api_shift_taint_range(const TaintRangePtr& source_taint_range, const RANGE_START offset, const RANGE_LENGTH new_length)
{
    return shift_taint_range(source_taint_range, offset, new_length);
}

TaintRangeRefs
shift_taint_ranges(const TaintRangeRefs& source_taint_ranges, RANGE_START offset, RANGE_LENGTH new_length);

TaintRangeRefs
api_shift_taint_ranges(const TaintRangeRefs&, RANGE_START offset, RANGE_LENGTH new_length);

std::pair<TaintRangeRefs, bool>
get_ranges(PyObject* string_input, const TaintRangeMapTypePtr& tx_map);

bool
set_ranges(PyObject* str, const TaintRangeRefs& ranges, const TaintRangeMapTypePtr& tx_map);

py::object
api_set_ranges(py::object& str, const TaintRangeRefs& ranges);

TaintRangeRefs
api_get_ranges(const py::object& string_input);

void
api_copy_ranges_from_strings(py::object& str_1, py::object& str_2);

inline void
api_copy_and_shift_ranges_from_strings(py::object& str_1, py::object& str_2, int offset, int new_length);

PyObject*
api_set_ranges_from_values(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

// Returns a tuple with (all ranges, ranges of candidate_text)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(PyObject* candidate_text, const py::tuple& parameter_list);
inline std::tuple<TaintRangeRefs, TaintRangeRefs>
api_are_all_text_all_ranges(py::object& candidate_text, const py::tuple& parameter_list)
{
    return are_all_text_all_ranges(candidate_text.ptr(), parameter_list);
}

TaintRangePtr
get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges);

inline void
api_set_fast_tainted_if_unicode(const py::object& obj)
{
    set_fast_tainted_if_notinterned_unicode(obj.ptr());
}

inline bool
api_is_unicode_and_not_fast_tainted(const py::object& str)
{
    return is_notinterned_notfasttainted_unicode(str.ptr());
}

TaintedObject*
get_tainted_object(PyObject* str, const TaintRangeMapTypePtr& tx_taint_map);

Py_hash_t
bytearray_hash(PyObject* bytearray);

Py_hash_t
get_internal_hash(PyObject* obj);

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, const TaintRangeMapTypePtr& tx_map);

void
pyexport_taintrange(py::module& m);
