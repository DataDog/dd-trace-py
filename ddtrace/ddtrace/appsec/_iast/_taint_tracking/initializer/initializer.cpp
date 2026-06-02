#include "initializer.h"

#include <thread>

using namespace std;
using namespace pybind11::literals;

Initializer::Initializer()
{
    // Fill the taintedobjects stack
    for (int i = 0; i < TAINTEDOBJECTS_STACK_SIZE; i++) {
        available_taintedobjects_stack.push(make_shared<TaintedObject>());
    }

    // Fill the ranges stack
    for (int i = 0; i < TAINTRANGES_STACK_SIZE; i++) {
        available_ranges_stack.push(make_shared<TaintRange>());
    }
}

TaintedObjectPtr
Initializer::allocate_tainted_object()
{
    if (!available_taintedobjects_stack.empty()) {
        const auto toptr = available_taintedobjects_stack.top();
        available_taintedobjects_stack.pop();
        return toptr;
    }
    // Stack is empty, create new object
    return make_shared<TaintedObject>();
}

TaintedObjectPtr
Initializer::allocate_ranges_into_taint_object(TaintRangeRefs ranges)
{
    const auto toptr = allocate_tainted_object();
    toptr->set_values(std::move(ranges));
    return toptr;
}

TaintedObjectPtr
Initializer::allocate_ranges_into_taint_object_copy(const TaintRangeRefs& ranges)
{
    const auto toptr = allocate_tainted_object();
    toptr->copy_values(ranges);
    return toptr;
}

TaintedObjectPtr
Initializer::allocate_tainted_object_copy(const TaintedObjectPtr& from)
{
    if (!from) {
        return allocate_tainted_object();
    }
    return allocate_ranges_into_taint_object_copy(from->ranges_);
}

TaintRangePtr
Initializer::allocate_taint_range(const RANGE_START start,
                                  const RANGE_LENGTH length,
                                  const Source& origin,
                                  const SecureMarks secure_marks)
{
    if (!available_ranges_stack.empty()) {
        auto rptr = available_ranges_stack.top();
        available_ranges_stack.pop();
        rptr->set_values(start, length, origin, secure_marks);
        return rptr;
    }

    // Stack is empty, create new object
    return make_shared<TaintRange>(start, length, origin, secure_marks);
}

void
Initializer::release_taint_range(TaintRangePtr rangeptr)
{
    if (!rangeptr)
        return;

    if (rangeptr.use_count() == 1) {
        rangeptr->reset();
        if (available_ranges_stack.size() < TAINTRANGES_STACK_SIZE) {
            // Move the range to the allocated ranges stack
            available_ranges_stack.push(rangeptr);
            return;
        }

        // Stack full or initializer already cleared (interpreter finishing), just
        // release the object
        rangeptr.reset(); // Not duplicated or typo, calling reset on the shared_ptr, not the TaintRange
    }
}

// Created in the PYBIND11_MODULE in _native.cpp
unique_ptr<Initializer> initializer;