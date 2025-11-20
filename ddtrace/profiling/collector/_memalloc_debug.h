#pragma once

#include <cassert>

#include <Python.h>

/* Release the GIL. For debugging when GIL release allows memory profiling functions
 * to interleave from different threads. Call near C Python API calls. */
static inline void
memalloc_debug_gil_release(void)
{
#ifndef NDEBUG
    Py_BEGIN_ALLOW_THREADS;
    Py_END_ALLOW_THREADS;
#endif
}

class memalloc_gil_debug_check_t
{
  public:
    memalloc_gil_debug_check_t() = default;

    bool acquired = false;
};

#ifndef NDEBUG
/* RAII guard for GIL debug checking. Automatically acquires the guard in the
 * constructor and releases it in the destructor. */
class memalloc_gil_debug_guard_t
{
  public:
    explicit memalloc_gil_debug_guard_t(memalloc_gil_debug_check_t& guard)
      : guard_(guard)
    {
        assert(PyGILState_Check());
        assert(!guard_.acquired);
        guard_.acquired = true;
    }

    ~memalloc_gil_debug_guard_t()
    {
        assert(guard_.acquired);
        guard_.acquired = false;
    }

    // Non-copyable, non-movable
    memalloc_gil_debug_guard_t(const memalloc_gil_debug_guard_t&) = delete;
    memalloc_gil_debug_guard_t& operator=(const memalloc_gil_debug_guard_t&) = delete;
    memalloc_gil_debug_guard_t(memalloc_gil_debug_guard_t&&) = delete;
    memalloc_gil_debug_guard_t& operator=(memalloc_gil_debug_guard_t&&) = delete;

  private:
    memalloc_gil_debug_check_t& guard_;
};
#else
/* In release builds, the guard is a no-op */
class memalloc_gil_debug_guard_t
{
  public:
    explicit memalloc_gil_debug_guard_t(memalloc_gil_debug_check_t&) {}

    // Non-copyable, non-movable
    memalloc_gil_debug_guard_t(const memalloc_gil_debug_guard_t&) = delete;
    memalloc_gil_debug_guard_t& operator=(const memalloc_gil_debug_guard_t&) = delete;
    memalloc_gil_debug_guard_t(memalloc_gil_debug_guard_t&&) = delete;
    memalloc_gil_debug_guard_t& operator=(memalloc_gil_debug_guard_t&&) = delete;
};
#endif
