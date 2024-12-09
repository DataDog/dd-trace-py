#pragma once
#include <sstream>
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
using TaintedObjectPtr = shared_ptr<TaintedObject>;

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

std::pair<TaintRangeRefs, bool>
get_ranges(PyObject* string_input, const TaintRangeMapTypePtr& tx_map);

bool
set_ranges(PyObject* str, const TaintRangeRefs& ranges, const TaintRangeMapTypePtr& tx_map);

py::object
api_set_ranges(py::handle& str, const TaintRangeRefs& ranges);

TaintRangeRefs
api_get_ranges(const py::handle& string_input);

inline void
api_set_fast_tainted_if_unicode(const py::handle& obj)
{
    set_fast_tainted_if_notinterned_unicode(obj.ptr());
}

inline bool
api_is_unicode_and_not_fast_tainted(const py::handle& str)
{
    return is_notinterned_notfasttainted_unicode(str.ptr());
}

TaintedObjectPtr
get_tainted_object(PyObject* str, const TaintRangeMapTypePtr& tx_taint_map);

Py_hash_t
bytearray_hash(PyObject* bytearray);

Py_hash_t
get_internal_hash(PyObject* obj);

void
set_tainted_object(PyObject* str, TaintedObjectPtr tainted_object, const TaintRangeMapTypePtr& tx_map);

void
pyexport_taintrange(py::module& m);
