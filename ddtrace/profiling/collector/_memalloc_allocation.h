#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

#include <Python.h>

#include "sample.hpp"

/* Allocation sample - fire-and-forget, no tracking needed
 * Records allocation events without tracking object lifetime */
class allocation_sample_t
{
  public:
    Datadog::Sample sample;

    /* Constructor - collects allocation metrics and stacktrace
     * NOTE: Invokes CPython APIs which may release the GIL during frame collection */
    allocation_sample_t(size_t size, size_t weighted_size, uint16_t max_nframe);

    ~allocation_sample_t() = default;

    // Non-copyable, non-movable
    allocation_sample_t(const allocation_sample_t&) = delete;
    allocation_sample_t& operator=(const allocation_sample_t&) = delete;
    allocation_sample_t(allocation_sample_t&&) = delete;
    allocation_sample_t& operator=(allocation_sample_t&&) = delete;

    /* Initialize allocation sample module (creates interned strings)
     * Returns true on success, false otherwise
     * NOTE: Invokes CPython APIs */
    [[nodiscard]] static bool init_invokes_cpython();

    /* Deinitialize allocation sample module
     * NOTE: Invokes CPython APIs */
    static void deinit_invokes_cpython();

  private:
    /* Common initialization logic
     * _invokes_cpython suffix: calls CPython APIs which may release the GIL during frame collection */
    void init_sample_invokes_cpython(size_t size, size_t weighted_size);
};
