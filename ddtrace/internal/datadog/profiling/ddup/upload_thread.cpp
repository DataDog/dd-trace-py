#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "upload_thread.hpp"

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <new> // IWYU pragma: keep  -- required for placement new in postfork_child
#include <pthread.h>
#include <thread>

// On glibc/libstdc++, pthread_exit() unwinds via a special C++ exception
// (abi::__forced_unwind) that must never be swallowed by a catch-all. libc++
// (macOS) has no such type, so the guard is glibc-only.
#if defined(__GLIBC__)
#include <cxxabi.h>
#endif

#if PY_VERSION_HEX >= 0x030d0000
#define py_is_finalizing Py_IsFinalizing
#else
#define py_is_finalizing _Py_IsFinalizing
#endif

namespace {

struct UploadThread
{
    std::mutex mtx;
    std::condition_variable cv;
    bool running = false;
    bool stop_requested = false;
    double interval_s = 1.0;
    // Owned reference to the Python tick callable, held while the thread runs.
    PyObject* tick = nullptr;
    std::unique_ptr<std::thread> thread;
    bool was_running_at_fork = false;
};

// Process-global uploader thread. The profiler is a process-wide singleton, so
// a single instance suffices. It is constructed with constant initializers, so
// it is ready before any fork handler or worker thread runs.
UploadThread g_upload;

std::once_flag g_atfork_once;

inline bool
is_finalizing()
{
    return py_is_finalizing();
}

// Detach (or, as a last resort, leak) a thread handle without joining. Used in
// the forked child where the worker thread no longer exists but the inherited
// std::thread handle is still "joinable" and would std::terminate() on
// destruction.
void
safe_reset_thread(std::unique_ptr<std::thread>& thread_ptr) noexcept
{
    if (!thread_ptr) {
        return;
    }
    try {
        if (thread_ptr->joinable()) {
            thread_ptr->detach();
        }
    } catch (...) {
        // Handle is invalid (e.g. inherited from the fork parent): intentionally
        // leak the std::thread object rather than crash the process.
        [[maybe_unused]] std::thread* leaked = thread_ptr.release();
        return;
    }
    try {
        thread_ptr.reset();
    } catch (...) {
        [[maybe_unused]] std::thread* leaked = thread_ptr.release();
    }
}

// Call the Python tick callable under the GIL and return the next interval in
// seconds. Returns a negative value to keep the current interval.
double
call_tick()
{
    // Never touch the Python VM once it has started finalizing.
    if (is_finalizing() || g_upload.tick == nullptr) {
        return -1.0;
    }

    PyGILState_STATE gstate;
    try {
        gstate = PyGILState_Ensure();
#if defined(__GLIBC__)
    } catch (const abi::__forced_unwind&) {
        // pthread_exit() unwinding on glibc: must re-throw, never swallow.
        throw;
#endif
    } catch (...) {
        return -1.0;
    }

    double next = -1.0;
    PyObject* result = PyObject_CallObject(g_upload.tick, nullptr);
    if (result == nullptr) {
        if (PyErr_Occurred()) {
            PyErr_Print();
        }
    } else {
        // The tick returns the next interval in seconds as a float (an int is
        // also accepted, via __float__). PyFloat_AsDouble avoids the C-style
        // casts baked into the Py*_Check macros; a negative/failed result
        // leaves the current interval unchanged.
        next = PyFloat_AsDouble(result);
        if (PyErr_Occurred()) {
            PyErr_Clear();
            next = -1.0;
        }
        Py_DecRef(result);
    }

    PyGILState_Release(gstate);
    return next;
}

void
upload_thread_main()
{
    std::unique_lock<std::mutex> lock(g_upload.mtx);
    while (!g_upload.stop_requested) {
        const auto timeout =
          std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::duration<double>(g_upload.interval_s));
        g_upload.cv.wait_for(lock, timeout, [] { return g_upload.stop_requested; });
        if (g_upload.stop_requested) {
            break;
        }
        if (is_finalizing()) {
            break;
        }

        // Release the lock around the Python call: the tick acquires the GIL
        // and may block on the network send, and stop() needs the lock to
        // signal us. The worker therefore never holds the GIL and the mutex at
        // the same time, which keeps the fork prepare handler deadlock-free.
        lock.unlock();
        const double next = call_tick();
        lock.lock();

        if (next >= 0.0) {
            g_upload.interval_s = next;
        }
    }
    g_upload.running = false;
}

// Launch the worker. Caller must hold g_upload.mtx and must have already set
// tick/interval.
void
launch_locked()
{
    g_upload.stop_requested = false;
    g_upload.running = true;
    try {
        g_upload.thread = std::make_unique<std::thread>(upload_thread_main);
    } catch (...) {
        g_upload.running = false;
    }
}

void
upload_thread_prefork()
{
    // Runs on the forking thread (GIL held). The worker only ever holds mtx for
    // brief bookkeeping that needs no GIL, so acquiring it here cannot deadlock
    // against the GIL we hold. The lock is released in the parent/child
    // post-fork handlers.
    g_upload.mtx.lock();
    g_upload.was_running_at_fork = g_upload.running;
}

void
upload_thread_postfork_parent()
{
    g_upload.mtx.unlock();
}

void
upload_thread_postfork_child()
{
    // The worker thread does not exist in the child. Operating on inherited
    // sync primitives is undefined, so recreate them from scratch, then drop
    // the dead thread handle without joining.
    new (&g_upload.mtx) std::mutex();
    new (&g_upload.cv) std::condition_variable();
    safe_reset_thread(g_upload.thread);
    g_upload.running = false;
    g_upload.stop_requested = false;

    if (g_upload.was_running_at_fork && g_upload.tick != nullptr) {
        std::lock_guard<std::mutex> lock(g_upload.mtx);
        launch_locked();
    }
    g_upload.was_running_at_fork = false;
}

} // namespace

extern "C"
{
    void ddup_upload_thread_start(double interval_s, PyObject* tick) // cppcheck-suppress unusedFunction
    {
        // Called with the GIL held. Install the fork handlers exactly once.
        std::call_once(g_atfork_once, [] {
            pthread_atfork(upload_thread_prefork, upload_thread_postfork_parent, upload_thread_postfork_child);
        });

        std::lock_guard<std::mutex> lock(g_upload.mtx);
        if (g_upload.running) {
            return;
        }

        // Own a reference to the callable *before* starting the thread: the
        // worker may run (and read tick) before this function returns. Py_IncRef
        // / Py_DecRef are the function forms of Py_XINCREF / Py_DECREF and avoid
        // the C-style casts in those macros.
        Py_IncRef(tick);
        if (g_upload.tick != nullptr) {
            PyObject* old = g_upload.tick;
            g_upload.tick = nullptr;
            Py_DecRef(old); // GIL is held by the caller.
        }
        g_upload.tick = tick;
        g_upload.interval_s = interval_s > 0.0 ? interval_s : 1.0;

        launch_locked();
    }

    void ddup_upload_thread_stop() // cppcheck-suppress unusedFunction
    {
        // Must be called with the GIL released so the worker can finish an
        // in-flight tick (which needs the GIL) and then observe stop_requested.
        std::unique_ptr<std::thread> to_join;
        {
            std::lock_guard<std::mutex> lock(g_upload.mtx);
            if (!g_upload.thread) {
                return;
            }
            g_upload.stop_requested = true;
            g_upload.cv.notify_all();
            to_join = std::move(g_upload.thread);
        }
        if (to_join && to_join->joinable()) {
            try {
                to_join->join();
            } catch (...) {
            }
        }
    }
}
