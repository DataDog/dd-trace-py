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
    traceback_t(void* ptr,
                size_t size,
                PyMemAllocatorDomain domain,
                size_t weighted_size,
                PyFrameObject* pyframe,
                uint16_t max_nframe);

#if PY_VERSION_HEX >= 0x030b0000 && PY_VERSION_HEX < 0x030d0000
    /* Constructor for Python 3.11-3.12 - uses _PyInterpreterFrame directly to avoid allocations
     * Note: Python 3.13+ has different internal structures, so we use PyFrameObject instead */
    traceback_t(void* ptr,
                size_t size,
                PyMemAllocatorDomain domain,
                size_t weighted_size,
                PyThreadState* tstate,
                uint16_t max_nframe);
#endif

    /* Destructor - cleans up Python references */
    ~traceback_t();

    /* Factory method - creates a traceback from the current Python frame chain */
    static traceback_t* get_traceback(uint16_t max_nframe,
                                      void* ptr,
                                      size_t size,
                                      PyMemAllocatorDomain domain,
                                      size_t weighted_size);

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
