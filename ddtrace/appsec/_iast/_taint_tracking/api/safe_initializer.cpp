#include "api/safe_initializer.h"

TaintRangePtr
safe_allocate_taint_range(RANGE_START start, RANGE_LENGTH length, const Source& source, const SecureMarks secure_marks)
{
    if (!initializer) {
        return nullptr;
    }
    return initializer->allocate_taint_range(start, length, source, secure_marks);
}

TaintedObjectPtr
safe_allocate_tainted_object_copy(const TaintedObjectPtr& from)
{
    if (!initializer) {
        return nullptr;
    }
    return initializer->allocate_tainted_object_copy(from);
}

TaintedObjectPtr
safe_allocate_tainted_object()
{
    if (!initializer) {
        return nullptr;
    }
    return initializer->allocate_tainted_object();
}

TaintedObjectPtr
safe_allocate_ranges_into_taint_object(TaintRangeRefs ranges)
{
    if (!initializer) {
        return nullptr;
    }
    return initializer->allocate_ranges_into_taint_object(ranges);
}

TaintedObjectPtr
safe_allocate_ranges_into_taint_object_copy(const TaintRangeRefs& ranges)
{
    if (!initializer) {
        return nullptr;
    }
    return initializer->allocate_ranges_into_taint_object_copy(ranges);
}
