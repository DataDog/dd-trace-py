#pragma once

#include <cstddef>
#include <cstdint>

#include <Python.h>

// Include Sample class header to enable calling functions from Sample.cpp
#include "sample.hpp"

class traceback_t
{
  public:
    /* Sample object storing the stacktrace */
    Datadog::Sample sample;

    /* Intrusive doubly-linked list pointers for heap tracker.
     * Used instead of a hashmap to allow O(1) insert/remove and
     * signature-based free-path lookup. */
    traceback_t* list_next = nullptr;
    traceback_t* list_prev = nullptr;

    /* Pointer returned to Python (real_ptr + MEMALLOC_HEADER_SIZE).
     * Stored so postfork_child can clear the signature in the header
     * without needing to walk Python's allocator. */
    void* user_ptr = nullptr;

    /* Original allocation size requested by Python (excluding header).
     * Needed by realloc to know how many bytes to copy when transitioning
     * between sampled/non-sampled states. */
    size_t alloc_size = 0;

    /* Constructor - also collects frames from the current Python frame chain. */
    traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe);

    ~traceback_t() = default;

    /* Initialize/populate this traceback with allocation data and collect frames.
     * Assumes sample buffers are already clean (cleared when returned to pool).
     * Stack walking uses direct CPython struct reads to avoid allocator reentry
     * from refcount churn while still collecting Python frames. */
    void init_sample(size_t size, size_t weighted_size, uint16_t max_nframe);

    // Non-copyable, non-movable
    traceback_t(const traceback_t&) = delete;
    traceback_t& operator=(const traceback_t&) = delete;
    traceback_t(traceback_t&&) = delete;
    traceback_t& operator=(traceback_t&&) = delete;
};

/* The maximum number of frames we can collect for a traceback
 * Limited by the backend's maximum accepted frame count */
#define TRACEBACK_MAX_NFRAME 600

/* Hard cap on raw frame-chain traversal.
 * Keep this separate from TRACEBACK_MAX_NFRAME so skipped or malformed frames
 * cannot leave the allocator-hook walk effectively unbounded. Set above the
 * backend frame limit while still keeping allocator-hook traversal finite. */
#define TRACEBACK_MAX_WALKED_NFRAME 1024

/* The maximum number of traceback samples we can store in the heap profiler */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX
