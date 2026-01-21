#pragma once

#include <cstdint>
#include <memory>

class allocation_profiler_t
{
  public:
    /* Constructor */
    allocation_profiler_t(uint32_t sample_interval_val);
    ~allocation_profiler_t() = default;

    // Delete copy constructor and assignment operator
    allocation_profiler_t(const allocation_profiler_t&) = delete;
    allocation_profiler_t& operator=(const allocation_profiler_t&) = delete;

    /* Decide whether we should sample an allocation of the given size.
     * Returns true if we should sample, and sets allocated_memory_val to the
     * current allocated_memory value for weighting. */
    bool should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val);

    /* Track an allocation by creating and exporting a sample immediately.
     * This is fire-and-forget - no ptr tracking needed.
     * NOTE: Invokes CPython APIs during stacktrace collection. */
    void track_allocation_invokes_cpython(size_t size, uint64_t allocated_memory_val, uint16_t max_nframe);

    /* Global instance of the allocation profiler */
    static allocation_profiler_t* instance;

  private:
    static uint32_t next_sample_interval_no_cpython(uint32_t sample_interval);

    /* Allocation profiler sampling interval */
    uint64_t sample_interval;
    /* Next allocation sample target, in bytes allocated */
    uint64_t current_sample_interval;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;
};

/* Public API */
bool
memalloc_allocation_profiler_init_no_cpython(uint32_t sample_interval);

void
memalloc_allocation_profiler_deinit_no_cpython(void);
