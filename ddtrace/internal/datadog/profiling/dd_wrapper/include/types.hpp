#pragma once

namespace Datadog {
enum SampleType : unsigned int
{
    CPU = 1 << 0,
    Wall = 1 << 1,
    Exception = 1 << 2,
    LockAcquire = 1 << 3,
    LockRelease = 1 << 4,
    Allocation = 1 << 5,
    Heap = 1 << 6,
    All = CPU | Wall | Exception | LockAcquire | LockRelease | Allocation | Heap
};

// Rather than wrapping and unwrapping values when they're flushed, we just use a vector.
// Features are implemented by way of indices, which are stored in the shared state
struct ValueIndex
{
    unsigned short cpu_time;
    unsigned short cpu_count;
    unsigned short wall_time;
    unsigned short wall_count;
    unsigned short exception_count;
    unsigned short lock_acquire_time;
    unsigned short lock_acquire_count;
    unsigned short lock_release_time;
    unsigned short lock_release_count;
    unsigned short alloc_space;
    unsigned short alloc_count;
    unsigned short heap_space;
};

} // namespace Datadog
