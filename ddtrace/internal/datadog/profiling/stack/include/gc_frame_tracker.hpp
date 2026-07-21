#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mutex>
#include <unordered_map>

namespace Datadog {

class GCFrameTracker
{
#if PY_VERSION_HEX < 0x030f0000
    struct State
    {
        PyObject* callback = nullptr;
        PyObject* frame = nullptr;
    };

    std::unordered_map<PyInterpreterState*, State> states_;
    std::mutex mutex_;
#endif

    GCFrameTracker() = default;

  public:
    GCFrameTracker(const GCFrameTracker&) = delete;
    GCFrameTracker& operator=(const GCFrameTracker&) = delete;

    static GCFrameTracker& get();

    // These methods run with the calling interpreter attached. On 3.15+ the
    // interpreter exposes gc.frame directly, so no callback is installed.
    bool install_current_interpreter();
    bool uninstall_current_interpreter();

    // Return a borrowed, address-only frame pointer. The sampling thread must
    // never dereference this directly; normal safe-copy frame walking does so.
    PyObject* capture(PyInterpreterState* interp);

#if PY_VERSION_HEX < 0x030f0000
    // Called by the native gc.callbacks callable in the attached interpreter.
    void update(PyInterpreterState* interp, PyObject* frame);
#endif

    // Fork handlers. prepare()/parent() keep fallback state stable across the
    // fork; child() recreates synchronization and clears stale frame pointers.
    void prefork();
    void postfork_parent();
    void postfork_child();
};

} // namespace Datadog
