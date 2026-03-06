#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "structmember.h"

#include <cstring>
#include <stddef.h>

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <thread>

// On glibc, pthread_exit() is implemented via forced stack unwinding using
// abi::__forced_unwind (defined in <cxxabi.h>). If this exception escapes a
// std::thread callable, std::terminate is called. We catch it explicitly to
// allow the forced unwind to complete cleanly.
#if defined(__GLIBC__)
#include <cxxabi.h>
#endif

// Platform-specific includes for thread naming
#if defined(__linux__)
#include <pthread.h>
#elif defined(__APPLE__)
#include <pthread.h>
#elif defined(_WIN32)
#include <windows.h>
#elif defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__)
#include <pthread.h>
#include <pthread_np.h>
#endif

#if PY_VERSION_HEX >= 0x30d0000
#define py_is_finalizing Py_IsFinalizing
#else
#define py_is_finalizing _Py_IsFinalizing
#endif

// ----------------------------------------------------------------------------
/**
 * Truncate thread names with format "module.path:ClassName".
 * Prioritizes keeping the part after the colon (class name) as it's more useful.
 */
static void
truncate_at_class_name(char* dest, size_t dest_size, const char* name)
{
    size_t name_len = strlen(name);

    // If the name fits, just copy it
    if (name_len < dest_size) {
        strcpy(dest, name);
        return;
    }

    // Look for a colon separator (e.g., "some.module:SomeThreadSubclass")
    const char* colon = strchr(name, ':');
    if (colon != NULL) {
        // Skip the colon to get the class name part
        const char* class_name = colon + 1;
        size_t class_name_len = strlen(class_name);

        // If the class name fits, use it; otherwise truncate it
        if (class_name_len < dest_size) {
            strcpy(dest, class_name);
        } else {
            strncpy(dest, class_name, dest_size - 1);
            dest[dest_size - 1] = '\0';
        }
    } else {
        // No colon found, just truncate from the start
        strncpy(dest, name, dest_size - 1);
        dest[dest_size - 1] = '\0';
    }
}

// ----------------------------------------------------------------------------
/**
 * Set the native thread name for the current thread.
 * This is a cross-platform utility that handles platform-specific APIs.
 */
static void
set_native_thread_name(PyObject* name_obj)
{
    if (name_obj == Py_None || name_obj == NULL) {
        return;
    }

    // Extract the thread name as a UTF-8 C string
    const char* name = PyUnicode_AsUTF8(name_obj);
    if (name == NULL) {
        PyErr_Clear(); // Clear any error and continue without setting the name
        return;
    }

#if defined(__linux__)
    // Linux: pthread_setname_np with thread handle and name (max 15 chars + null terminator)
    char truncated_name[16];
    truncate_at_class_name(truncated_name, sizeof(truncated_name), name);
    pthread_setname_np(pthread_self(), truncated_name);

#elif defined(__APPLE__)
    // macOS: pthread_setname_np with just the name (applies to current thread)
    // macOS has a longer limit but we'll still truncate for safety
    char truncated_name[64];
    truncate_at_class_name(truncated_name, sizeof(truncated_name), name);
    pthread_setname_np(truncated_name);

#elif defined(_WIN32)
    // Windows 10+: SetThreadDescription (no length limit in practice)
    // Convert UTF-8 to wide string
    int wlen = MultiByteToWideChar(CP_UTF8, 0, name, -1, NULL, 0);
    if (wlen > 0) {
        wchar_t* wname = (wchar_t*)malloc(wlen * sizeof(wchar_t));
        if (wname != NULL) {
            MultiByteToWideChar(CP_UTF8, 0, name, -1, wname, wlen);
            SetThreadDescription(GetCurrentThread(), wname);
            free(wname);
        }
    }

#elif defined(__FreeBSD__)
    // FreeBSD: pthread_set_name_np (no documented length limit, but use truncation for safety)
    char truncated_name[64];
    truncate_at_class_name(truncated_name, sizeof(truncated_name), name);
    pthread_set_name_np(pthread_self(), truncated_name);

#elif defined(__OpenBSD__)
    // OpenBSD: pthread_setname_np with just the name (similar limits to Linux)
    char truncated_name[32];
    truncate_at_class_name(truncated_name, sizeof(truncated_name), name);
    pthread_setname_np(pthread_self(), truncated_name);

#elif defined(__NetBSD__)
    // NetBSD: pthread_setname_np with format string
    char truncated_name[32];
    truncate_at_class_name(truncated_name, sizeof(truncated_name), name);
    pthread_setname_np(pthread_self(), "%s", (void*)truncated_name);

#else
    // Unsupported platform: do nothing
    (void)name; // Suppress unused variable warning
#endif
}

// ----------------------------------------------------------------------------
/**
 * Ensure that the GIL is held.
 */
class GILGuard
{
  public:
    inline GILGuard()
      : _acquired(false)
    {
        if (!py_is_finalizing()) {
            _state = PyGILState_Ensure();
            _acquired = true;
        }
    }
    inline ~GILGuard()
    {
        if (_acquired && !py_is_finalizing() && PyGILState_Check())
            PyGILState_Release(_state);
    }

    // Returns true if the GIL was successfully acquired. When false, the
    // interpreter is finalizing and no Python API calls should be made.
    inline bool acquired() const { return _acquired; }

  private:
    PyGILState_STATE _state;
    bool _acquired;
};

// ----------------------------------------------------------------------------
/**
 * Release the GIL to allow other threads to run.
 */
class AllowThreads
{
  public:
    inline AllowThreads()
      : _state(nullptr)
      , _saved(false)
    {
        if (!py_is_finalizing()) {
            _state = PyEval_SaveThread();
            _saved = true;
        }
    }
    inline ~AllowThreads()
    {
        // AIDEV-NOTE: If the interpreter started finalizing while we had the
        // GIL released (e.g. during Event::wait()), do NOT call
        // PyEval_RestoreThread — it enters take_gil() which calls
        // PyThread_exit_thread() → pthread_exit(), triggering forced stack
        // unwinding through C++ destructors and std::terminate. Leaking the
        // thread state is harmless during shutdown.
        if (_saved && !py_is_finalizing())
            PyEval_RestoreThread(_state);
    }

  private:
    PyThreadState* _state;
    bool _saved;
};

// ----------------------------------------------------------------------------
class PyRef
{
  public:
    // Acquires a new reference (increments refcount).
    inline PyRef(PyObject* obj)
      : _obj(obj)
    {
        Py_INCREF(_obj);
    }
    // Steals an existing reference (does NOT increment refcount).
    // Use when the caller has already incremented the refcount.
    inline PyRef(PyObject* obj, bool stolen)
      : _obj(obj)
    {
        if (!stolen)
            Py_INCREF(_obj);
    }
    inline ~PyRef()
    {
        // Avoid calling Py_DECREF during finalization as the thread state
        // may be NULL, causing crashes in Python 3.14+ where _Py_Dealloc
        // dereferences tstate immediately. This check uses relaxed atomics
        // so it's not perfectly synchronized, but provides a safety net.
        if (!py_is_finalizing())
            Py_DECREF(_obj);
    }

  private:
    PyObject* _obj;
};

// Reasons associated with the _request wake channel.
static constexpr unsigned char REQUEST_REASON_NONE = 0;
static constexpr unsigned char REQUEST_REASON_AWAKE = 1 << 0;
static constexpr unsigned char REQUEST_REASON_STOP = 1 << 1;
static constexpr unsigned char REQUEST_REASON_FORK_STOP = 1 << 2;

// ----------------------------------------------------------------------------
class Event
{
  public:
    void set(unsigned char reasons = REQUEST_REASON_AWAKE)
    {
        std::lock_guard<std::mutex> lock(_mutex);

        unsigned char old_reasons = _reasons;
        _reasons |= reasons;

        if (old_reasons == _reasons)
            return;

        _cond.notify_all();
    }

    void wait()
    {
        std::unique_lock<std::mutex> lock(_mutex);
        _cond.wait(lock, [this]() { return _reasons != REQUEST_REASON_NONE; });
    }

    bool wait(std::chrono::milliseconds timeout)
    {
        std::unique_lock<std::mutex> lock(_mutex);
        return _cond.wait_for(lock, timeout, [this]() { return _reasons != REQUEST_REASON_NONE; });
    }

    bool wait(std::chrono::time_point<std::chrono::steady_clock> until)
    {
        std::unique_lock<std::mutex> lock(_mutex);
        return _cond.wait_until(lock, until, [this]() { return _reasons != REQUEST_REASON_NONE; });
    }

    void clear()
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _reasons = REQUEST_REASON_NONE;
    }

    void clear(unsigned char reasons)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _reasons &= static_cast<unsigned char>(~reasons);
    }

    unsigned char consume(unsigned char reasons)
    {
        std::lock_guard<std::mutex> lock(_mutex);

        unsigned char matched = _reasons & reasons;
        _reasons &= static_cast<unsigned char>(~reasons);

        return matched;
    }

    unsigned char consume_all()
    {
        std::lock_guard<std::mutex> lock(_mutex);

        unsigned char reasons = _reasons;
        _reasons = REQUEST_REASON_NONE;

        return reasons;
    }

  private:
    std::condition_variable _cond;
    std::mutex _mutex;
    unsigned char _reasons = REQUEST_REASON_NONE;
};

// ----------------------------------------------------------------------------
typedef struct periodic_thread
{
    PyObject_HEAD

      double interval;
    PyObject* name;
    PyObject* ident;

    PyObject* _target;
    PyObject* _on_shutdown;
    bool _no_wait_at_start;

    PyObject* _ddtrace_profiling_ignore;

    bool _stopping;
    bool _atexit;
    bool _skip_shutdown;

    std::chrono::time_point<std::chrono::steady_clock> _next_call_time;

    std::unique_ptr<Event> _started;
    std::unique_ptr<Event> _stopped;
    std::unique_ptr<Event> _request;
    std::unique_ptr<Event> _served;

    std::unique_ptr<std::mutex> _awake_mutex;

    std::unique_ptr<std::thread> _thread;
} PeriodicThread;

// ----------------------------------------------------------------------------
// Maintain a mapping of thread ID to PeriodicThread objects. This is similar
// to threading._active.
static PyObject* _periodic_threads = NULL;

// ----------------------------------------------------------------------------
static PyMemberDef PeriodicThread_members[] = {
    { "interval", T_DOUBLE, offsetof(PeriodicThread, interval), 0, "thread interval" },

    { "name", T_OBJECT_EX, offsetof(PeriodicThread, name), 0, "thread name" },
    { "ident", T_OBJECT_EX, offsetof(PeriodicThread, ident), 0, "thread ID" },
    { "no_wait_at_start", T_BOOL, offsetof(PeriodicThread, _no_wait_at_start), 0, "do not wait at start" },

    { "_ddtrace_profiling_ignore",
      T_OBJECT_EX,
      offsetof(PeriodicThread, _ddtrace_profiling_ignore),
      0,
      "whether to ignore the thread for profiling" },

    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static int
PeriodicThread_init(PeriodicThread* self, PyObject* args, PyObject* kwargs)
{
    static const char* kwlist[] = { "interval", "target", "name", "on_shutdown", "no_wait_at_start", NULL };

    self->name = Py_None;
    self->_on_shutdown = Py_None;

    if (!PyArg_ParseTupleAndKeywords(args,
                                     kwargs,
                                     "dO|OOp",
                                     (char**)kwlist,
                                     &self->interval,
                                     &self->_target,
                                     &self->name,
                                     &self->_on_shutdown,
                                     &self->_no_wait_at_start))
        return -1;

    Py_INCREF(self->_target);
    Py_INCREF(self->name);
    Py_INCREF(self->_on_shutdown);

    Py_INCREF(Py_None);
    self->ident = Py_None;

    Py_INCREF(Py_True);
    self->_ddtrace_profiling_ignore = Py_True;

    self->_stopping = false;
    self->_atexit = false;
    self->_skip_shutdown = false;

    self->_started = std::make_unique<Event>();
    self->_stopped = std::make_unique<Event>();
    self->_request = std::make_unique<Event>();
    self->_served = std::make_unique<Event>();

    self->_awake_mutex = std::make_unique<std::mutex>();

    return 0;
}

// ----------------------------------------------------------------------------
static inline bool
PeriodicThread__periodic(PeriodicThread* self)
{
    PyObject* result = PyObject_CallObject(self->_target, NULL);

    if (result == NULL) {
        PyErr_Print();
    }

    Py_XDECREF(result);

    return result == NULL;
}

// ----------------------------------------------------------------------------
static inline void
PeriodicThread__on_shutdown(PeriodicThread* self)
{
    PyObject* result = PyObject_CallObject(self->_on_shutdown, NULL);

    if (result == NULL) {
        PyErr_Print();
    }

    Py_XDECREF(result);
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_start(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    if (self->_thread != nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread already started");
        return NULL;
    }

    if (self->_stopping)
        Py_RETURN_NONE;

    // Initialize the next call time to the current time plus the interval.
    // This ensures that the first call happens after the specified interval.
    self->_next_call_time =
      std::chrono::steady_clock::now() + std::chrono::milliseconds((long long)(self->interval * 1000));

    // AIDEV-NOTE: Prevent use-after-free by incrementing the refcount BEFORE
    // creating the thread. This guarantees self stays alive for the entire
    // thread lifetime. Previously, the Py_INCREF happened inside the thread
    // lambda (via PyRef), leaving a window between thread creation and
    // refcount increment where self could theoretically be deallocated.
    Py_INCREF((PyObject*)self);

    // Start the thread
    self->_thread = std::make_unique<std::thread>([self]() {
        // AIDEV-NOTE: The entire thread body is wrapped in try/catch to handle
        // forced unwinding during interpreter finalization. When the TOCTTOU
        // race hits (py_is_finalizing() returns false but finalization starts
        // before PyEval_RestoreThread/PyGILState_Ensure completes), CPython's
        // take_gil() calls PyThread_exit_thread() → pthread_exit(). On glibc,
        // this triggers forced stack unwinding via abi::__forced_unwind. Without
        // this catch, the exception escapes the std::thread callable and hits
        // std::terminate.
        try {
            GILGuard _gil;

            // If the interpreter is already finalizing when this thread starts,
            // we could not acquire the GIL. Bail out immediately — calling any
            // Python API without the GIL would crash.
            if (!_gil.acquired()) {
                // The reference acquired before thread creation is
                // intentionally leaked here. We can't safely Py_DECREF
                // without the GIL, and we're only here because the
                // interpreter is finalizing — the process is exiting.
                self->_served->set();
                self->_started->set();
                self->_stopped->set();
                return;
            }

            // PyRef takes ownership of the reference we acquired before thread
            // creation (stolen reference — no additional Py_INCREF).
            // During finalization, ~PyRef skips Py_DECREF, intentionally
            // leaking the reference (harmless at shutdown).
            PyRef _((PyObject*)self, /*stolen=*/true);

            // Retrieve the thread ID
            {
                Py_DECREF(self->ident);
                self->ident = PyLong_FromLong((long)PyThreadState_Get()->thread_id);

                // Map the PeriodicThread object to its thread ID
                PyDict_SetItem(_periodic_threads, self->ident, (PyObject*)self);
            }

            // Set the native thread name for better debugging and profiling
            set_native_thread_name(self->name);

            // Mark the thread as started from this point.
            self->_started->set();

            bool error = false;
            if (self->_no_wait_at_start)
                self->_request->set(REQUEST_REASON_AWAKE);

            while (!self->_stopping) {
                {
                    AllowThreads _;

                    if (self->_request->wait(self->_next_call_time)) {
                        if (self->_stopping) {
                            // _stopping can be set by:
                            // 1. pre-fork stop: preserve non-fork reasons (e.g. awake)
                            //    so they survive restart;
                            // 2. regular stop(): consume all pending reasons.
                            const unsigned char stop_reasons =
                              self->_request->consume(REQUEST_REASON_FORK_STOP | REQUEST_REASON_STOP);
                            const bool has_fork_stop = (stop_reasons & REQUEST_REASON_FORK_STOP) != 0;
                            if (!has_fork_stop)
                                self->_request->consume_all();
                            break;
                        }

                        // Request wakeup while running (awake/no_wait_at_start).
                        // Timer wakeups are the wait(...) == false branch.
                        self->_request->consume_all();
                    }
                }

                if (py_is_finalizing())
                    break;

                if (PeriodicThread__periodic(self)) {
                    // Error
                    error = true;
                    break;
                }

                self->_next_call_time =
                  std::chrono::steady_clock::now() + std::chrono::milliseconds((long long)(self->interval * 1000));

                // If this came from a request mark it as served
                self->_served->set();
            }

            // Set request served in case any threads are waiting while a thread is
            // stopping.
            self->_served->set();

            if (!self->_atexit && !py_is_finalizing()) {
                // Run the shutdown callback if there was no error and we are not
                // at Python shutdown.
                if (!error && self->_on_shutdown != Py_None && !self->_skip_shutdown)
                    PeriodicThread__on_shutdown(self);

                // Remove the thread from the mapping of active threads
                PyDict_DelItem(_periodic_threads, self->ident);
            }

            // Notify the join method that the thread has stopped
            self->_stopped->set();
        }
#if defined(__GLIBC__)
        catch (abi::__forced_unwind&) {
            // CPython's take_gil calls pthread_exit() on non-main threads
            // during finalization. glibc implements this as a forced stack
            // unwind via __forced_unwind. We must re-throw: glibc aborts if
            // a forced unwind is swallowed. The re-throw propagates through
            // libstdc++'s std::thread wrapper which has its own
            // catch(__forced_unwind&){throw;} and lets the unwind complete.
            self->_served->set();
            self->_started->set();
            self->_stopped->set();
            throw;
        }
#endif
        catch (...) {
            // Safety net for any other unexpected exceptions during shutdown.
            self->_served->set();
            self->_started->set();
            self->_stopped->set();
        }
    });

    // Detach the thread. We will make our own joinable mechanism.
    self->_thread->detach();

    // Wait for the thread to start
    {
        AllowThreads _;

        self->_started->wait();
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_awake(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread not started");
        return NULL;
    }

    {
        AllowThreads _;
        std::lock_guard<std::mutex> lock(*self->_awake_mutex);

        self->_served->clear();
        self->_request->set(REQUEST_REASON_AWAKE);

        self->_served->wait();
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_stop(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread not started");
        return NULL;
    }

    self->_stopping = true;
    self->_request->set(REQUEST_REASON_STOP);

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_join(PeriodicThread* self, PyObject* args, PyObject* kwargs)
{
    if (self->_thread == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Periodic thread not started");
        return NULL;
    }

    if (self->_thread->get_id() == std::this_thread::get_id()) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot join the current periodic thread");
        return NULL;
    }

    PyObject* timeout = Py_None;

    if (args != NULL && kwargs != NULL) {
        static const char* argnames[] = { "timeout", NULL };
        if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", (char**)argnames, &timeout))
            return NULL;
    }

    if (timeout == Py_None) {
        AllowThreads _;

        self->_stopped->wait();
    } else {
        double timeout_value = 0.0;

        if (PyFloat_Check(timeout)) {
            timeout_value = PyFloat_AsDouble(timeout);
        } else if (PyLong_Check(timeout)) {
            timeout_value = PyLong_AsDouble(timeout);
        } else {
            PyErr_SetString(PyExc_TypeError, "timeout must be a float or an int");
            return NULL;
        }

        AllowThreads _;

        auto interval = std::chrono::milliseconds((long long)(timeout_value * 1000));

        self->_stopped->wait(interval);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__atexit(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    self->_atexit = true;

    // The thread may not have been started, or _after_fork may have already
    // cleared _thread. Tolerate this gracefully.
    if (self->_thread == nullptr)
        Py_RETURN_NONE;

    if (PeriodicThread_stop(self, NULL) == NULL)
        return NULL;

    if (PeriodicThread_join(self, NULL, NULL) == NULL)
        return NULL;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
// Common reset logic shared by _after_fork and _after_fork_child.
static inline void
PeriodicThread__reset_state(PeriodicThread* self)
{
    self->_thread = nullptr;
    self->_stopping = false;
    self->_atexit = false;
    self->_skip_shutdown = false;
}

// ----------------------------------------------------------------------------
// AIDEV-NOTE: _after_fork vs _after_fork_child
//
// After fork(), POSIX says mutexes held by threads that don't exist in the
// child are in an undefined state. The _before_fork handler stops and joins
// all threads before fork(), so mutexes SHOULD be released. But if a thread
// was mid-mutex-operation despite the join (a bug in the stop/join logic),
// the mutex state in the child is corrupted.
//
// _after_fork (parent path): Uses .clear() which locks the internal mutex.
// This is safe because all threads were joined, and other threads in the
// parent (e.g., awake() callers) may still hold references to these Event
// objects — we can't destroy them.
//
// _after_fork_child (child path): Recreates all Events and mutexes from
// scratch. This is safe because no other threads exist in the child after
// fork — only the forking thread survives. Recreating avoids locking
// potentially corrupted mutexes entirely.
// ----------------------------------------------------------------------------

static PyObject*
PeriodicThread__after_fork(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    PeriodicThread__reset_state(self);

    // Parent path: clear existing Events (preserves references held by other threads).
    self->_request->clear(REQUEST_REASON_FORK_STOP);
    self->_started->clear();
    self->_stopped->clear();
    self->_served->clear();

    // Propagate start failures — fork-based frameworks (gunicorn, uWSGI,
    // Celery) depend on periodic threads restarting in every worker.
    return PeriodicThread_start(self, NULL);
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__after_fork_child(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    PeriodicThread__reset_state(self);

    // Child path: recreate all synchronization primitives from scratch.
    // No other threads exist in the child after fork(), so no one holds
    // references to the old Events. This avoids locking potentially
    // corrupted mutexes entirely.
    self->_started = std::make_unique<Event>();
    self->_stopped = std::make_unique<Event>();
    self->_request = std::make_unique<Event>();
    self->_served = std::make_unique<Event>();

    self->_awake_mutex = std::make_unique<std::mutex>();

    // Propagate start failures — fork-based frameworks (gunicorn, uWSGI,
    // Celery) depend on periodic threads restarting in every worker.
    return PeriodicThread_start(self, NULL);
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__before_fork(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    self->_skip_shutdown = true;

    // Synchronize with awake() so there is no window where _stopping is visible
    // before the fork-stop wake reason is published.
    {
        AllowThreads _;
        std::lock_guard<std::mutex> lock(*self->_awake_mutex);

        // Equivalent to PeriodicThread_stop(), with an explicit fork-stop
        // reason. Keep this order so the worker cannot consume fork-stop as a
        // normal wakeup.
        self->_stopping = true;
        self->_request->set(REQUEST_REASON_FORK_STOP);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static void
PeriodicThread_dealloc(PeriodicThread* self)
{
    // Since the native thread holds a strong reference to this object, we
    // can only get here if the thread has actually stopped.

    if (py_is_finalizing()) {
        // Do nothing. We are about to terminate and release resources anyway.
        return;
    }

    // If we are trying to stop from the same thread, then we are still running.
    // This should happen rarely, so we don't worry about the memory leak this
    // will cause.
    if (self->_thread != NULL && self->_thread->get_id() == std::this_thread::get_id())
        return;

    // Unmap the PeriodicThread
    if (self->ident != NULL && PyDict_Contains(_periodic_threads, self->ident))
        PyDict_DelItem(_periodic_threads, self->ident);

    Py_XDECREF(self->name);
    Py_XDECREF(self->_target);
    Py_XDECREF(self->_on_shutdown);

    Py_XDECREF(self->ident);
    Py_XDECREF(self->_ddtrace_profiling_ignore);

    self->_thread = nullptr;

    self->_started = nullptr;
    self->_stopped = nullptr;
    self->_request = nullptr;
    self->_served = nullptr;

    self->_awake_mutex = nullptr;

    Py_TYPE(self)->tp_free((PyObject*)self);
}

// ----------------------------------------------------------------------------
static PyMethodDef PeriodicThread_methods[] = {
    { "start", (PyCFunction)PeriodicThread_start, METH_NOARGS, "Start the thread" },
    { "awake", (PyCFunction)PeriodicThread_awake, METH_NOARGS, "Awake the thread" },
    { "stop", (PyCFunction)PeriodicThread_stop, METH_NOARGS, "Stop the thread" },
    { "join", (PyCFunction)PeriodicThread_join, METH_VARARGS | METH_KEYWORDS, "Join the thread" },
    /* Private */
    { "_atexit", (PyCFunction)PeriodicThread__atexit, METH_NOARGS, "Stop the thread at exit" },
    { "_after_fork", (PyCFunction)PeriodicThread__after_fork, METH_NOARGS, "Refresh the thread after fork (parent)" },
    { "_after_fork_child",
      (PyCFunction)PeriodicThread__after_fork_child,
      METH_NOARGS,
      "Refresh the thread after fork (child)" },
    { "_before_fork", (PyCFunction)PeriodicThread__before_fork, METH_NOARGS, "Prepare the thread for fork" },
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyTypeObject PeriodicThreadType = {
    .ob_base = PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal._threads.PeriodicThread",
    .tp_basicsize = sizeof(PeriodicThread),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)PeriodicThread_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = PyDoc_STR("Native thread calling a Python function periodically"),
    .tp_methods = PeriodicThread_methods,
    .tp_members = PeriodicThread_members,
    .tp_init = (initproc)PeriodicThread_init,
    .tp_new = PyType_GenericNew,
};

// ----------------------------------------------------------------------------
static PyMethodDef _threads_methods[] = {
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static struct PyModuleDef threadsmodule = {
    PyModuleDef_HEAD_INIT,
    "_threads", /* name of module */
    NULL,       /* module documentation, may be NULL */
    -1,         /* size of per-interpreter state of the module,
                   or -1 if the module keeps state in global variables. */
    _threads_methods,
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit__threads(void)
{
    PyObject* m = NULL;

    if (PyType_Ready(&PeriodicThreadType) < 0)
        return NULL;

    _periodic_threads = PyDict_New();
    if (_periodic_threads == NULL)
        return NULL;

    m = PyModule_Create(&threadsmodule);
    if (m == NULL)
        goto error;

    Py_INCREF(&PeriodicThreadType);
    if (PyModule_AddObject(m, "PeriodicThread", (PyObject*)&PeriodicThreadType) < 0) {
        Py_DECREF(&PeriodicThreadType);
        goto error;
    }

    if (PyModule_AddObject(m, "periodic_threads", _periodic_threads) < 0)
        goto error;

    return m;

error:
    Py_XDECREF(_periodic_threads);
    Py_XDECREF(m);

    return NULL;
}
