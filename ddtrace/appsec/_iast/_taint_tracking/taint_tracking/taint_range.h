#pragma once
#include <sstream>
#include <utility>

#include <pybind11/stl.h>

#include "structmember.h"

#include "constants.h"
#include "taint_tracking/source.h"
#include "utils/string_utils.h"

using namespace std;
namespace py = pybind11;

// Forward declarations
class TaintedObject;

// Alias
using TaintedObjectPtr = shared_ptr<TaintedObject>;

// Use Abseil only if NDEBUG is set and DONT_COMPILE_ABSEIL is not set
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/node_hash_map.h"
using TaintRangeMapType = absl::node_hash_map<uintptr_t, std::pair<Py_hash_t, TaintedObjectPtr>>;
#else
#include <unordered_map>
using TaintRangeMapType = std::unordered_map<uintptr_t, std::pair<Py_hash_t, TaintedObjectPtr>>;
#endif

/**
 * @brief Enumeration of vulnerability types that can be marked in taint ranges
 * @details Each value represents a different type of security vulnerability that can be detected
 *          and marked in tainted data ranges. The values are used as bit positions in the secure_marks
 *          bitfield.
 */
enum class VulnerabilityType
{
    CODE_INJECTION = 1,   ///< Code injection vulnerability
    COMMAND_INJECTION,    ///< Command injection vulnerability
    HEADER_INJECTION,     ///< HTTP header injection vulnerability
    UNVALIDATED_REDIRECT, ///< Unvalidate redirect
    INSECURE_COOKIE,      ///< Insecure cookie configuration
    NO_HTTPONLY_COOKIE,   ///< Missing HttpOnly flag in cookie
    NO_SAMESITE_COOKIE,   ///< Missing SameSite attribute in cookie
    PATH_TRAVERSAL,       ///< Path traversal vulnerability
    SQL_INJECTION,        ///< SQL injection vulnerability
    SSRF,                 ///< Server-Side Request Forgery vulnerability
    STACKTRACE_LEAK,      ///< Stack trace information leakage
    WEAK_CIPHER,          ///< Weak cryptographic cipher usage
    WEAK_HASH,            ///< Weak cryptographic hash function usage
    WEAK_RANDOMNESS,      ///< Weak random number generation
    XSS,                  ///< Cross-Site Scripting vulnerability
};

using TaintRangeMapTypePtr = shared_ptr<TaintRangeMapType>;
using SecureMarks = uint64_t;

/**
 * @brief Represents a range of tainted data with associated security marks
 * @details This class manages a range of tainted data with its source and security marks.
 *          It provides methods for manipulation and validation of taint ranges.
 */
struct TaintRange
{
    RANGE_START start = 0;
    RANGE_LENGTH length = 0;
    Source source;
    SecureMarks secure_marks;

    TaintRange()
      : start(0)
      , length(0)
      , source()
      , secure_marks(0)
    {
    }

    TaintRange(const RANGE_START start, const RANGE_LENGTH length, Source source, const SecureMarks& secure_marks = 0)
      : start(start)
      , length(length)
      , source(std::move(source))
      , secure_marks(secure_marks)
    {
        if (length <= 0) {
            throw std::invalid_argument("Error: Length cannot be set to 0.");
        }
    }

    inline void set_values(const RANGE_START start_,
                           const RANGE_LENGTH length_,
                           Source source_,
                           SecureMarks secure_marks_) noexcept
    {
        start = start_;
        length = length_;
        source = std::move(source_);
        secure_marks = secure_marks_;
    }

    void reset();
    string toString() const;
    uint get_hash() const;
    /**
     * @brief Adds a security vulnerability mark to the taint range
     * @param mark The type of vulnerability to mark
     * @details Sets the corresponding bit in the secure_marks bitfield based on the vulnerability type.
     *          The mark is stored efficiently using bit operations.
     */
    void add_secure_mark(VulnerabilityType mark);
    /**
     * @brief Checks if a specific vulnerability mark is set in the taint range
     * @param mark The type of vulnerability to check for
     * @return true if the vulnerability mark is set, false otherwise
     * @details Checks the corresponding bit in the secure_marks bitfield using bit operations.
     */
    bool has_secure_mark(VulnerabilityType mark) const;
    bool has_origin(OriginType origin) const;
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
api_set_ranges(py::handle& str, const TaintRangeRefs& ranges);

TaintRangeRefs
api_get_ranges(const py::handle& string_input);

void
api_copy_ranges_from_strings(py::handle& str_1, py::handle& str_2);

inline void
api_copy_and_shift_ranges_from_strings(py::handle& str_1, py::handle& str_2, int offset, int new_length);

PyObject*
api_set_ranges_from_values(PyObject* self, PyObject* const* args, Py_ssize_t nargs);

// Returns a tuple with (all ranges, ranges of candidate_text)
std::tuple<TaintRangeRefs, TaintRangeRefs>
are_all_text_all_ranges(PyObject* candidate_text, const py::tuple& parameter_list);
inline std::tuple<TaintRangeRefs, TaintRangeRefs>
api_are_all_text_all_ranges(py::handle& candidate_text, const py::tuple& parameter_list)
{
    return are_all_text_all_ranges(candidate_text.ptr(), parameter_list);
}

TaintRangePtr
get_range_by_hash(size_t range_hash, optional<TaintRangeRefs>& taint_ranges);

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

inline void
copy_and_shift_ranges_from_strings(const py::handle& str_1,
                                   const py::handle& str_2,
                                   const int offset,
                                   const int new_length,
                                   const TaintRangeMapTypePtr& tx_map)
{
    if (!tx_map)
        return;

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

void
pyexport_taintrange(py::module& m);
