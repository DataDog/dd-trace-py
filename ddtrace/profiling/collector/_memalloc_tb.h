#pragma once

#include <cstddef>
#include <cstdint>

#include <Python.h>

// Include Sample class header to enable calling functions from Sample.cpp
#include "sample.hpp"

class traceback_t
{
  public:
    /* True if this sample has been reported previously */
    bool reported;
    /* Sample object storing the stacktrace */
    Datadog::Sample sample;

    /* Constructor - also collects frames from the current Python frame chain
     * NOTE: Invokes CPython APIs which may release the GIL during frame collection */
    traceback_t(size_t size, size_t weighted_size, uint16_t max_nframe);

    ~traceback_t() = default;

    /* Reset/clear this traceback for reuse with a new allocation
     * Clears all sample data and re-collects frames from the current Python frame chain
     * NOTE: Invokes CPython APIs which may release the GIL during frame collection */
    void reset_invokes_cpython(size_t size, size_t weighted_size);

    /* Initialize traceback module (creates interned strings)
     * Returns true on success, false otherwise
     * NOTE: Invokes CPython APIs */
    [[nodiscard]] static bool init_invokes_cpython();
    /* Deinitialize traceback module
     * NOTE: Invokes CPython APIs */
    static void deinit_invokes_cpython();

    // Non-copyable, non-movable
    traceback_t(const traceback_t&) = delete;
    traceback_t& operator=(const traceback_t&) = delete;
    traceback_t(traceback_t&&) = delete;
    traceback_t& operator=(traceback_t&&) = delete;

  private:
    /* Common initialization logic shared by constructor and reset
     * _invokes_cpython suffix: calls CPython APIs which may release the GIL during frame collection */
    void init_sample_invokes_cpython(size_t size, size_t weighted_size);
};

/* The maximum number of frames we can collect for a traceback
 * Limited by the backend's maximum accepted frame count */
#define TRACEBACK_MAX_NFRAME 600

/* The maximum number of traceback samples we can store in the heap profiler */
#define TRACEBACK_ARRAY_MAX_COUNT UINT16_MAX
