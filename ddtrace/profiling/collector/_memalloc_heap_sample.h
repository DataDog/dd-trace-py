#pragma once

#include <cstddef>
#include <cstdint>

#include <Python.h>

#include "sample.hpp"

/* Heap sample - tracked until object is freed
 * Records live heap allocations that need to be tracked */
class heap_sample_t
{
  public:
    Datadog::Sample sample;

    /* Constructor - collects heap metrics and stacktrace
     * NOTE: Invokes CPython APIs which may release the GIL during frame collection */
    heap_sample_t(size_t size, uint16_t max_nframe);

    ~heap_sample_t() = default;

    // Non-copyable, non-movable
    heap_sample_t(const heap_sample_t&) = delete;
    heap_sample_t& operator=(const heap_sample_t&) = delete;
    heap_sample_t(heap_sample_t&&) = delete;
    heap_sample_t& operator=(heap_sample_t&&) = delete;

    /* Initialize heap sample module (creates interned strings)
     * Returns true on success, false otherwise
     * NOTE: Invokes CPython APIs */
    [[nodiscard]] static bool init_invokes_cpython();

    /* Deinitialize heap sample module
     * NOTE: Invokes CPython APIs */
    static void deinit_invokes_cpython();

  private:
    /* Common initialization logic
     * _invokes_cpython suffix: calls CPython APIs which may release the GIL during frame collection */
    void init_sample_invokes_cpython(size_t size);
};
