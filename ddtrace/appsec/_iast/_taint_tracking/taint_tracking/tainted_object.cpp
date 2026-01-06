#include "api/safe_initializer.h"
#include "initializer/initializer.h"
#include <cstdlib>

namespace py = pybind11;

// Default max range count if environment variable is not set
constexpr int DEFAULT_MAX_RANGE_COUNT = 30;

// Static variables for caching the taint range limit
namespace {
int g_cached_limit = 0;
bool g_limit_initialized = false;
}

// Get the max range count from environment variable
int
get_taint_range_limit()
{
    if (g_cached_limit == 0) {
        const char* env_value = std::getenv("DD_IAST_MAX_RANGE_COUNT");
        if (env_value != nullptr) {
            try {
                long parsed_value = std::strtol(env_value, nullptr, 10);
                if (parsed_value > 0) {
                    g_cached_limit = static_cast<int>(parsed_value);
                } else {
                    g_cached_limit = DEFAULT_MAX_RANGE_COUNT;
                }
            } catch (...) {
                g_cached_limit = DEFAULT_MAX_RANGE_COUNT;
            }
        } else {
            g_cached_limit = DEFAULT_MAX_RANGE_COUNT;
        }
    }

    return g_cached_limit;
}

// Reset the cached taint range limit (for testing purposes only)
void
reset_taint_range_limit_cache()
{
    g_cached_limit = 0;
}

/**
 * This function allocates a new taint range with the given offset and maximum length.
 *
 * @param source_taint_range The source taint range.
 * @param offset The offset to be applied.
 * @param max_length The maximum length of the taint range.
 *
 * @return A new taint range with the given offset and maximum length.
 */
TaintRangePtr
allocate_limited_taint_range_with_offset(const TaintRangePtr& source_taint_range,
                                         const RANGE_START offset,
                                         const RANGE_LENGTH max_length)
{
    RANGE_LENGTH length;
    if (max_length != -1)
        length = min(max_length, source_taint_range->length);
    else
        length = source_taint_range->length;

    auto tptr = safe_allocate_taint_range(offset, length, source_taint_range->source, source_taint_range->secure_marks);
    return tptr;
}

/**
 * @brief Shifts the taint range by the given offset.
 * @param source_taint_range The source taint range.
 * @param offset The offset to be applied.
 */
TaintRangePtr
shift_taint_range(const TaintRangePtr& source_taint_range, const RANGE_START offset)
{
    auto tptr = safe_allocate_taint_range(source_taint_range->start + offset,
                                          source_taint_range->length,
                                          source_taint_range->source,
                                          source_taint_range->secure_marks);
    return tptr;
}

/**
 * This function shifts the taint ranges by the given offset.
 *
 * @param tainted_object The tainted object.
 * @param offset The offset to be applied.
 * @param max_length The maximum length of the taint range.
 * @param orig_offset The offset to be applied at the beginning.
 */
void
TaintedObject::add_ranges_shifted(const TaintedObjectPtr tainted_object,
                                  const RANGE_START offset,
                                  const RANGE_LENGTH max_length,
                                  const RANGE_START orig_offset)
{
    const auto& ranges = tainted_object->get_ranges();
    add_ranges_shifted(ranges, offset, max_length, orig_offset);
}

/**
 * This function shifts the taint ranges by the given offset.
 *
 * @param ranges The taint ranges.
 * @param offset The offset to be applied.
 * @param max_length The maximum length of the taint range.
 * @param orig_offset The offset to be applied at the beginning.
 */
void
TaintedObject::add_ranges_shifted(TaintRangeRefs ranges,
                                  const RANGE_START offset,
                                  const RANGE_LENGTH max_length,
                                  const RANGE_START orig_offset)
{
    if (const auto to_add = static_cast<long>(min(ranges.size(), get_free_tainted_ranges_space()));
        !ranges.empty() and to_add > 0) {
        ranges_.reserve(ranges_.size() + to_add);
        if (offset == 0 and max_length == -1) {
            ranges_.insert(ranges_.end(), ranges.begin(), ranges.end());
        } else {
            int i = 0;
            for (const auto& trange : ranges) {
                if (max_length != -1 and orig_offset != -1) {
                    // Make sure original position (orig_offset) is covered by the range
                    if (trange->start <= orig_offset and
                        ((trange->start + trange->length) >= orig_offset + max_length)) {
                        ranges_.emplace_back(allocate_limited_taint_range_with_offset(trange, offset, max_length));
                        i++;
                    }
                } else {
                    ranges_.emplace_back(shift_taint_range(trange, offset));
                    i++;
                }
                if (i >= to_add) {
                    break;
                }
            }
        }
    }
}

std::string
TaintedObject::toString() const
{
    stringstream ss;

    ss << "TaintedObject [";
    if (not ranges_.empty()) {
        ss << ", ranges=[";
        for (const auto& range : ranges_) {
            ss << range->toString() << ", ";
        }
        ss << "]";
    }
    ss << "]";

    return ss.str();
}

TaintedObject::operator string() const
{
    return toString();
}

void
TaintedObject::move_ranges_to_stack()
{
    for (const auto& range_ptr : ranges_) {
        if (!range_ptr) {
            continue;
        }
        initializer->release_taint_range(range_ptr);
    }
    ranges_.clear();
}

void
TaintedObject::reset()
{
    move_ranges_to_stack();
    if (initializer) {
        ranges_.reserve(RANGES_INITIAL_RESERVE);
    }
}