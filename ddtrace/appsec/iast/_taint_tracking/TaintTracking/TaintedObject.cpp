#include "TaintTracking/TaintedObject.h"
#include "Initializer/Initializer.h"
#include "TaintTracking/TaintRange.h"
#include <pybind11/pybind11.h>

namespace py = pybind11;

TaintRangePtr
shift_taint_range(const TaintRangePtr& source_taint_range, long offset)
{
    auto tptr = initializer->allocate_taint_range(source_taint_range->start + offset, // start
                                                  source_taint_range->length,         // length
                                                  source_taint_range->source);        // source
    return tptr;
}

void
TaintedObject::add_ranges_shifted(TaintedObjectPtr tainted_object, long offset)
{
    const auto& ranges = tainted_object->get_ranges();
    const auto to_add = (long)min(ranges.size(), TAINT_RANGE_LIMIT - ranges_.size());
    if (!ranges.empty() and to_add > 0) {
        ranges_.reserve(ranges_.size() + to_add);
        int i = 0;
        if (offset == 0) {
            ranges_.insert(ranges_.end(), ranges.begin(), ranges.end());
        } else {
            for (const auto& trange : ranges) {
                ranges_.emplace_back(shift_taint_range(trange, offset));
                i++;
                if (i >= to_add) {
                    break;
                }
            }
        }
    }
}

TaintedObject::operator string()
{
    stringstream ss;

    ss << "TaintedObject [";
    if (not ranges_.empty()) {
        ss << ", ranges=[";
        for (const auto& range : ranges_) {
            ss << range << ", ";
        }
        ss << "]";
    }
    ss << "]";

    return ss.str();
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
    //    if (initializer) {
    //        ranges_.reserve(RANGES_INITIAL_RESERVE);
    //    }
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
    // If rc_ is negative, there is a bug. We check it with an assert (let release builds to tolerate it).
    assert(rc_ == 0);
    // initializer->release_tainted_object(this);
}

void
pyexport_taintedobject(py::module& m)
{
    py::class_<TaintedObject>(m, "TaintedObject").def(py::init<>());
}
