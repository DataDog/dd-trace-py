#pragma once

#include <cstddef>
#include <cstdint>

#include <Python.h>

// Include Sample class header to enable calling functions from Sample.cpp
#include "../../internal/datadog/profiling/dd_wrapper/include/sample.hpp"

class traceback_t
{
  public:
    /* Memory pointer allocated */
    void* ptr;
    /* Memory size allocated in bytes */
    size_t size;
    /* Domain allocated */
    PyMemAllocatorDomain domain;
    /* True if this sample has been reported previously */
    bool reported;
    /* Count of allocations this sample represents (for scaling) */
    size_t count;
    /* Sample object storing the stacktrace */
    Datadog::Sample sample;

    /* Constructor - also collects frames from the current Python frame chain */
    traceback_t(void* ptr, size_t size, PyMemAllocatorDomain domain, size_t weighted_size, uint16_t max_nframe);

    /* Destructor - cleans up Python references */
    ~traceback_t();

    /* Initialize traceback module (creates interned strings)
     * Returns true on success, false otherwise */
    [[nodiscard]] static bool init();
    /* Deinitialize traceback module */
    static void deinit();

    // Non-copyable, non-movable
    traceback_t(const traceback_t&) = delete;
    traceback_t& operator=(const traceback_t&) = delete;
    traceback_t(traceback_t&&) = delete;
    traceback_t& operator=(traceback_t&&) = delete;
};

/* The maximum number of frames we can collect for a traceback */
#define TRACEBACK_MAX_NFRAME UINT16_MAX

/* The maximum number of traceback samples we can store in the heap profiler */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX
