#pragma once

#include "_pymacro.h"

#if defined(_PY310_AND_LATER)

/* RAII guard for temporarily disabling Python's garbage collector.
 * Automatically disables GC in the constructor and re-enables it in the destructor
 * (if it was previously enabled).
 *
 * This is useful for preventing GC from running during critical sections where
 * we're manipulating memory allocations, as GC can trigger arbitrary Python code
 * which could cause reentrancy issues or use-after-free bugs.
 *
 * Note: This guard is only available for Python 3.10+, when the PyGC_Disable/Enable
 * APIs were defined. The calling code decides when to use it based on Python version. */
class pygc_temp_disable_guard_t
{
  public:
    pygc_temp_disable_guard_t()
      : was_enabled_(false)
    {
        // PyGC_Disable() returns the previous state (1 if enabled, 0 if disabled)
        was_enabled_ = PyGC_Disable();
    }

    ~pygc_temp_disable_guard_t()
    {
        // Only re-enable if it was previously enabled
        if (was_enabled_) {
            PyGC_Enable();
        }
    }

    // Non-copyable, non-movable
    pygc_temp_disable_guard_t(const pygc_temp_disable_guard_t&) = delete;
    pygc_temp_disable_guard_t& operator=(const pygc_temp_disable_guard_t&) = delete;
    pygc_temp_disable_guard_t(pygc_temp_disable_guard_t&&) = delete;
    pygc_temp_disable_guard_t& operator=(pygc_temp_disable_guard_t&&) = delete;

  private:
    int was_enabled_;
};

#endif // _PY310_AND_LATER
