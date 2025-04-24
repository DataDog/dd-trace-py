#pragma once

namespace Datadog {

enum class SampleType : unsigned int
{
    Invalid = 0,
    CPU = 1 << 0,
    Wall = 1 << 1,
    Exception = 1 << 2,
    LockAcquire = 1 << 3,
    LockRelease = 1 << 4,
    Allocation = 1 << 5,
    Heap = 1 << 6,
    GPUTime = 1 << 7,
    GPUMemory = 1 << 8,
    GPUFlops = 1 << 9,
    All = CPU | Wall | Exception | LockAcquire | LockRelease | Allocation | Heap | GPUTime | GPUMemory | GPUFlops,
};

inline SampleType
operator|(SampleType a, SampleType b)
{
    return static_cast<SampleType>(static_cast<unsigned int>(a) | static_cast<unsigned int>(b));
}

inline SampleType
operator&(SampleType a, SampleType b)
{
    return static_cast<SampleType>(static_cast<unsigned int>(a) & static_cast<unsigned int>(b));
}

inline bool
is_valid_type(SampleType a)
{
    a = a & SampleType::All; // bits outside of the valid set get masked off
    return a != SampleType::Invalid;
}

inline bool
mask_has_type(SampleType mask, SampleType type)
{
    return is_valid_type(mask & type);
}

// Every Sample object has a corresponding `values` vector, since libdatadog expects contiguous values per sample.
// The index into that vector is determined by the configured sample types, which is encoded below.
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
    unsigned short gpu_time;
    unsigned short gpu_count;
    unsigned short gpu_alloc_space;
    unsigned short gpu_alloc_count;
    unsigned short gpu_flops;
    unsigned short gpu_flops_samples; // Should be "count," but flops is already a count
};

} // namespace Datadog
