#pragma once

#include "_pymacro.h"

/* RAII guard for temporarily disabling Python's garbage collector.
 * Automatically disables GC in the constructor and re-enables it in the destructor
 * (if it was previously enabled).
 *
 * Used during traceback collection in allocator hooks to prevent GC from running
 * mid-realloc and triggering use-after-free through stale pointers in moved
 * structures (PROF-11496, PR #14550). The OBJ realloc path rarely moves data so
 * the bug was originally specific to MEM realloc (list ob_item, dict ma_keys).
 *
 * Python 3.10+ uses the public PyGC_Disable()/PyGC_Enable() APIs.
 * Python 3.9 has no such public API, so we fall back to invoking
 * gc.disable()/gc.enable() via cached Python callables. The callables are
 * cached at first use into function-static PyObject* slots with strong refs
 * held for the lifetime of the process (mirrors the gc module's own lifetime
 * in sys.modules).
 *
 * Reentrancy: the call site (memalloc_heap_track_invokes_cpython) holds a
 * memalloc_reentrant_guard_t before instantiating this guard, so any inner
 * allocations from gc.disable()/enable() Python bytecode skip our hook.
 */
#if defined(_PY310_AND_LATER)

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

#else // Python 3.9: fall back to gc.disable() / gc.enable() via the C API.

class pygc_temp_disable_guard_t
{
  public:
    pygc_temp_disable_guard_t()
      : was_enabled_(false)
    {
        Callables c = get_callables();
        if (!c.disable || !c.enable || !c.isenabled) {
            return;
        }
        PyObject* state = PyObject_CallNoArgs(c.isenabled);
        if (!state) {
            PyErr_Clear();
            return;
        }
        was_enabled_ = (state == Py_True);
        Py_DECREF(state);
        if (was_enabled_) {
            PyObject* r = PyObject_CallNoArgs(c.disable);
            if (!r) {
                PyErr_Clear();
                was_enabled_ = false;
            } else {
                Py_DECREF(r);
            }
        }
    }

    ~pygc_temp_disable_guard_t()
    {
        if (!was_enabled_) {
            return;
        }
        Callables c = get_callables();
        if (!c.enable) {
            return;
        }
        PyObject* r = PyObject_CallNoArgs(c.enable);
        if (!r) {
            PyErr_Clear();
        } else {
            Py_DECREF(r);
        }
    }

    pygc_temp_disable_guard_t(const pygc_temp_disable_guard_t&) = delete;
    pygc_temp_disable_guard_t& operator=(const pygc_temp_disable_guard_t&) = delete;
    pygc_temp_disable_guard_t(pygc_temp_disable_guard_t&&) = delete;
    pygc_temp_disable_guard_t& operator=(pygc_temp_disable_guard_t&&) = delete;

  private:
    struct Callables
    {
        PyObject* disable;
        PyObject* enable;
        PyObject* isenabled;
    };

    /* Process-lifetime cache. Strong refs are held for the life of the process,
     * mirroring sys.modules['gc']. Initialised on first call under the GIL. */
    static Callables get_callables()
    {
        static PyObject* disable_fn = nullptr;
        static PyObject* enable_fn = nullptr;
        static PyObject* isenabled_fn = nullptr;
        if (disable_fn == nullptr) {
            PyObject* gc_module = PyImport_ImportModule("gc");
            if (gc_module) {
                disable_fn = PyObject_GetAttrString(gc_module, "disable");
                enable_fn = PyObject_GetAttrString(gc_module, "enable");
                isenabled_fn = PyObject_GetAttrString(gc_module, "isenabled");
                Py_DECREF(gc_module);
            }
            if (!disable_fn || !enable_fn || !isenabled_fn) {
                PyErr_Clear();
            }
        }
        return Callables{ disable_fn, enable_fn, isenabled_fn };
    }

    bool was_enabled_;
};

#endif // _PY310_AND_LATER
