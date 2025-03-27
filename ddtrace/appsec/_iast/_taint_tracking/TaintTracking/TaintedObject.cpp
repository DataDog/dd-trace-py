#include "Initializer/Initializer.h"

namespace py = pybind11;

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

    auto tptr =
      initializer->allocate_taint_range(offset, length, source_taint_range->source, source_taint_range->secure_marks);
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
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset,
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
    if (const auto to_add = static_cast<long>(min(ranges.size(), TAINT_RANGE_LIMIT - ranges_.size()));
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

void
pyexport_taintedobject(const py::module& m)
{
    py::class_<TaintedObject>(m, "TaintedObject").def(py::init<>());
}
