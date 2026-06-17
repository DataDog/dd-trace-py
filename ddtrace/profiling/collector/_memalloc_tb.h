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

    /* Persistent live-heap profile (Option A) bookkeeping. Only meaningful
     * while this traceback is owned by the heap tracker.
     *
     * pending_add_idx encodes both whether this allocation has been applied to
     * the persistent heap profile and, if not, where to find it for O(1)
     * removal:
     *   >= 0 : index of this traceback in the heap tracker's pending_adds
     *          vector. The allocation is a pending ADD that has not yet been
     *          applied, so a same-interval free collapses the add/free pair by
     *          removing it from pending_adds at this index.
     *   PENDING_ADD_APPLIED (-1): already applied to the persistent profile at
     *          export time (or not currently pending), so a later free must
     *          schedule a subtraction instead of collapsing. */
    static constexpr std::ptrdiff_t PENDING_ADD_APPLIED = -1;
    std::ptrdiff_t pending_add_idx = PENDING_ADD_APPLIED;

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
