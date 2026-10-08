#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <array>
#include <atomic>
#include <cstddef>
#include <functional>
#include <mutex>
#include <optional>

namespace Datadog {

class GCFrameTracker
{
#if PY_VERSION_HEX < 0x030f0000
    struct Slot
    {
        // Set once, when the slot is claimed, and never changed again to avoid races.
        std::atomic<PyInterpreterState*> interp{ nullptr };
        std::atomic<PyObject*> frame{ nullptr };
        // Owned reference, only read or written under mutex_.
        PyObject* callback = nullptr;
    };

    // Maximum number of interpreters that can be tracked during the process lifetime
    static constexpr std::size_t max_tracked_interpreters = 16;

    std::array<Slot, max_tracked_interpreters> slots_;
    std::atomic<std::size_t> slots_used_{ 0 };
    std::mutex mutex_;

    // Returns the slot of interp, if it has one. Safe to call without mutex_.
    std::optional<std::reference_wrapper<Slot>> find_slot(PyInterpreterState* interp);

    // Returns the slot of interp, claiming an unused one if needed, or nothing if the table is
    // full. Callers must hold mutex_.
    std::optional<std::reference_wrapper<Slot>> find_or_claim_slot(PyInterpreterState* interp);

    // Drops the callback and frame of a slot, keeping its interpreter. Callers must hold mutex_
    // and must own the callback reference that the slot held.
    static void clear_slot(Slot& slot);
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

    // Fork handlers. prefork()/postfork_parent() keep install and uninstall from being half done
    // across the fork; postfork_child() recreates synchronization and clears stale frame pointers.
    void prefork();
    void postfork_parent();
    void postfork_child();
};

} // namespace Datadog
