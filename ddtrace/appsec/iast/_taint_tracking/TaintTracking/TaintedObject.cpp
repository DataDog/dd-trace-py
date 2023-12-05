#include "TaintTracking/TaintedObject.h"
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include <pybind11/pybind11.h>

namespace py = pybind11;

TaintRangePtr
reposition_and_limit_taint_range(const TaintRangePtr& source_taint_range, RANGE_START offset, RANGE_LENGTH max_length)
{
    RANGE_LENGTH length;
    if (max_length != -1)
        length = min(max_length, source_taint_range->length);
    else
        length = source_taint_range->length;

    auto tptr = initializer->allocate_taint_range(offset,                      // start
                                                  length,                      // length
                                                  source_taint_range->source); // source
    return tptr;
}

TaintRangePtr
shift_taint_range(const TaintRangePtr& source_taint_range, RANGE_START offset)
{
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset, // start
                                                  source_taint_range->length,         // length
                                                  source_taint_range->source);        // source
    return tptr;
}

void
TaintedObject::add_ranges_shifted(TaintedObjectPtr tainted_object,
                                  RANGE_START offset,
                                  RANGE_LENGTH max_length,
                                  RANGE_START orig_offset)
{
    const auto& ranges = tainted_object->get_ranges();
    add_ranges_shifted(ranges, offset, max_length, orig_offset);
}

void
TaintedObject::add_ranges_shifted(TaintRangeRefs ranges,
                                  RANGE_START offset,
                                  RANGE_LENGTH max_length,
                                  RANGE_START orig_offset)
{
    const auto to_add = (long)min(ranges.size(), TAINT_RANGE_LIMIT - ranges_.size());
    if (!ranges.empty() and to_add > 0) {
        ranges_.reserve(ranges_.size() + to_add);
        int i = 0;
        if (offset == 0 and max_length == -1) {
            ranges_.insert(ranges_.end(), ranges.begin(), ranges.end());
        } else {
            for (const auto& trange : ranges) {
                if (max_length != -1 and orig_offset != -1) {
                    // Make sure original position (orig_offset) is covered by the range
                    if (trange->start <= orig_offset and
                        ((trange->start + trange->length) >= orig_offset + max_length)) {
                        ranges_.emplace_back(reposition_and_limit_taint_range(trange, offset, max_length));
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
TaintedObject::toString()
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

TaintedObject::operator string()
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
    rc_ = 0;
    if (initializer) {
        ranges_.reserve(RANGES_INITIAL_RESERVE);
    }
}

void
TaintedObject::incref()
{
    rc_++;
}

void
TaintedObject::decref()
{
    if (--rc_ <= 0) {
        release();
    }
}

void
TaintedObject::release()
{
    // If rc_ is negative, there is a bug.
    assert(rc_ == 0);
    initializer->release_tainted_object(this);
}

void
pyexport_taintedobject(py::module& m)
{
    py::class_<TaintedObject>(m, "TaintedObject").def(py::init<>());
}
