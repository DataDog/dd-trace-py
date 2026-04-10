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

// Forward declaration: PeriodicThread is defined later in this file.
typedef struct periodic_thread PeriodicThread;

struct module_state
{
    // At-exit barrier to avoid making Python VM calls during or after shutdown.
    // Set by the atexit handler after stopping threads, giving an extra
    // safety net before py_is_finalizing() is set by the VM.
    std::atomic<bool> at_exit{ false };

    // Mapping of active periodic thread IDs to their PeriodicThread objects.
    PyObject* periodic_threads{ nullptr };

    inline bool is_finalizing() const noexcept
    {
        // Our atexit handler fires first (stopping threads before VM sets its
        // finalizing flag). The at_exit barrier is set as extra assurance.
        // The py_is_finalizing macro is a fallback for cases where the atexit
        // handler never ran (e.g. uWSGI --skip-atexit).
        return at_exit.load(std::memory_order_acquire) || py_is_finalizing();
    }
};

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
    inline GILGuard(module_state* state)
      : _mstate(state)
    {
        if (!_mstate->is_finalizing())
            _gil_state = PyGILState_Ensure();
    }
    inline ~GILGuard()
    {
        if (!_mstate->is_finalizing() && PyGILState_Check())
            PyGILState_Release(_gil_state);
    }

  private:
    module_state* _mstate;
    PyGILState_STATE _gil_state;
};

// ----------------------------------------------------------------------------
/**
 * Release the GIL to allow other threads to run.
 */
class AllowThreads
{
  public:
    inline AllowThreads(module_state* state)
      : _mstate(state)
    {
        if (!_mstate->is_finalizing())
            _thread_state = PyEval_SaveThread();
    }
    inline ~AllowThreads()
    {
        if (!_mstate->is_finalizing())
            PyEval_RestoreThread(_thread_state);
    }

  private:
    module_state* _mstate;
    PyThreadState* _thread_state;
};

// ----------------------------------------------------------------------------
class PyRef
{
  public:
    inline PyRef(PyObject* obj, module_state* state)
      : _obj(obj)
      , _mstate(state)
    {
        Py_INCREF(_obj);
    }
    inline ~PyRef()
    {
        // Avoid calling Py_DECREF during finalization as the thread state
        // may be NULL, causing crashes in Python 3.14+ where _Py_Dealloc
        // dereferences tstate immediately.
        if (!_mstate->is_finalizing())
            Py_DECREF(_obj);
    }

  private:
    PyObject* _obj;
    module_state* _mstate;
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
    bool _skip_shutdown;

    module_state* _state;

    std::chrono::time_point<std::chrono::steady_clock> _next_call_time;

    std::unique_ptr<Event> _started;
    // AIDEV-NOTE: _stopped uses shared_ptr so the lambda can capture a copy.
    // When PyRef's Py_DECREF drops the Python refcount to zero during thread
    // teardown, dealloc resets self->_stopped (decrementing the shared_ptr
    // refcount), but the lambda's captured copy keeps the Event alive until
    // stopped_event->set() completes.
    std::shared_ptr<Event> _stopped;
    std::unique_ptr<Event> _request;
    std::unique_ptr<Event> _served;

    std::unique_ptr<std::mutex> _awake_mutex;

    std::unique_ptr<std::thread> _thread;
} PeriodicThread;

// Pointer to the module definition, needed for module state lookup
// in PeriodicThread_init. Set during PyInit__threads.
static PyModuleDef* threadsmodule_ptr = NULL;

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
    self->_skip_shutdown = false;

    // Look up this interpreter's module state. PyState_FindModule searches the
    // current interpreter, so each sub-interpreter gets its own module_state.
    PyObject* mod = PyState_FindModule(threadsmodule_ptr);
    if (mod == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "_threads module not initialized");
        return -1;
    }
    self->_state = (module_state*)PyModule_GetState(mod);

    self->_started = std::make_unique<Event>();
    self->_stopped = std::make_shared<Event>();
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
// Internal helper: launches the thread after ensuring preconditions.
// If reset_next_call_time is true (normal start), _next_call_time is initialised
// to now + interval before starting; otherwise it is left untouched (important
// for cases where the thread is being restarted after a fork to preserve the
// existing next trigger time).
static PyObject*
_PeriodicThread_do_start(PeriodicThread* self, bool reset_next_call_time = false)
{
    if (self->_thread != nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "Thread already started");
        return NULL;
    }

    if (self->_stopping)
        Py_RETURN_NONE;

    if (reset_next_call_time)
        self->_next_call_time =
          std::chrono::steady_clock::now() + std::chrono::milliseconds((long long)(self->interval * 1000));

    // Capture _stopped as a shared_ptr before starting the thread. If PyRef's
    // Py_DECREF drops the last Python reference inside the thread (triggering
    // dealloc and resetting self->_stopped), this captured copy keeps the Event
    // alive until stopped_event->set() completes.
    std::shared_ptr<Event> stopped_event = self->_stopped;

    // Start the thread
    self->_thread = std::make_unique<std::thread>([self, stopped_event]() {
        module_state* state = self->_state;

        // DEV: GILGuard and PyRef are in an inner scope that exits BEFORE
        // stopped_event->set(). This ensures that all Python VM interactions
        // (Py_DECREF, PyGILState_Release) complete before the join() caller is
        // unblocked. The inner scope also means PyRef::~PyRef may trigger
        // PeriodicThread_dealloc (if this thread held the last reference),
        // which is safe because stopped_event is a captured shared_ptr
        // independent of self's lifetime.
        {
            GILGuard _gil(state);

            PyRef _ref((PyObject*)self, state);

            // Retrieve the thread ID
            {
                Py_DECREF(self->ident);
                self->ident = PyLong_FromLong((long)PyThreadState_Get()->thread_id);

                // Map the PeriodicThread object to its thread ID
                PyDict_SetItem(state->periodic_threads, self->ident, (PyObject*)self);
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
                    AllowThreads _(state);

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

                if (state->is_finalizing())
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

            if (!state->is_finalizing()) {
                // Run the shutdown callback if there was no error and we are not
                // at Python shutdown.
                if (!error && self->_on_shutdown != Py_None && !self->_skip_shutdown)
                    PeriodicThread__on_shutdown(self);

                // Remove the thread from the mapping of active threads.
                PyDict_DelItem(state->periodic_threads, self->ident);
            }

            // Inner scope ends here. GILGuard::~GILGuard releases the GIL and
            // PyRef::~PyRef calls Py_DECREF(self). Both may interact with the
            // Python VM; they must complete before stopped_event->set() below.
        }

        // All Python VM interactions are done. Signal that the thread has fully
        // stopped. join() waits on this event; since the thread is detached
        // (no OS join), this is the sole synchronisation point.
        // DEV: The thread might have been destructed at this point, so we have
        // to interact with the stopped_event directly instead of self->_stopped
        // to avoid potential use-after-free of self.
        stopped_event->set();
    });

    // Detach immediately. The thread is self-managing: its OS resources are
    // released automatically on exit. join() synchronises via stopped_event
    // (set only after all Python VM teardown), so no OS join is ever needed.
    self->_thread->detach();

    // Wait for the thread to start
    {
        AllowThreads _(self->_state);

        self->_started->wait();
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread_start(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    return _PeriodicThread_do_start(self, true);
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
        AllowThreads _(self->_state);
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

    // The thread is always detached at creation time, so no OS join is needed.
    // _stopped is set only after all Python VM interactions complete (GIL
    // release, Py_DECREF), so waiting on it is sufficient for full teardown.
    if (timeout == Py_None) {
        AllowThreads _(self->_state);

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

        AllowThreads _(self->_state);

        auto interval = std::chrono::milliseconds((long long)(timeout_value * 1000));

        self->_stopped->wait(interval);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__after_fork(PeriodicThread* self, PyObject* args, PyObject* kwargs)
{
    // The parent process passes force=True to this method to override
    // __autorestart__ and always restart the thread. The parent must restore
    // every thread that was running before the fork, regardless of the
    // autorestart preference (which only governs the child). The default
    // force=False preserves the existing child-side behaviour: threads with
    // __autorestart__ = False are cleaned up but not restarted.
    int force = 0;
    static const char* kwlist[] = { "force", NULL };
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p", (char**)kwlist, &force))
        return NULL;

    // Check the __autorestart__ attribute (class or instance). Subclasses and
    // instances can set __autorestart__ = False to opt out of automatic
    // restart after fork in the child. When force=True (parent path) this
    // check is skipped and the thread is always restarted.
    bool should_restart = static_cast<bool>(force);
    if (!should_restart) {
        PyObject* autorestart = PyObject_GetAttrString((PyObject*)self, "__autorestart__");
        if (autorestart != NULL) {
            should_restart = (PyObject_IsTrue(autorestart) == 1);
            Py_DECREF(autorestart);
        } else {
            PyErr_Clear();
        }
    }

    // Always reset fork-specific state regardless of restart decision.
    self->_stopping = false;
    self->_skip_shutdown = false;
    // During prefork, _before_fork() sets REQUEST_REASON_FORK_STOP to wake
    // the thread promptly. Clear it so it does not trigger a spurious
    // periodic() call after restart (or linger in the no-restart case).
    self->_request->clear(REQUEST_REASON_FORK_STOP);

    // _before_fork() detaches every handle in the parent before the fork, so
    // joinable() is always false here. No OS call needed on the inherited handle.

    if (should_restart) {
        // Thread was detached at creation; just release the handle.
        self->_thread = nullptr;

        self->_started->clear();
        self->_stopped->clear();
        self->_served->clear();

        // Use _PeriodicThread_do_start instead of PeriodicThread_start to
        // preserve _next_call_time from before the fork. This ensures that
        // a restarted thread fires at the same time it would have without
        // the fork, rather than being pushed back by a full interval.
        _PeriodicThread_do_start(self);
    } else {
        // No restart: the common cleanup above is sufficient for fork-specific
        // state. Two additional invariants are preserved intentionally:
        //
        // AIDEV-NOTE: We do NOT null _thread. Threads are always detached at
        // creation so the handle is non-joinable. Keeping it non-null allows
        // stop() — which guards on _thread == nullptr — to be called without
        // raising "Thread not started".
        //
        // AIDEV-NOTE: We do NOT clear _stopped. It was set when the thread
        // exited in the parent; leaving it set means join() returns immediately
        // rather than blocking indefinitely.

        // Remove the stale parent-process ident from periodic_threads so
        // this thread is not picked up by subsequent fork cycles. The thread
        // removes itself on exit, so the entry may already be gone — ignore
        // the KeyError in that case.
        if (self->ident != Py_None && self->_state != nullptr && self->_state->periodic_threads != NULL) {
            if (PyDict_DelItem(self->_state->periodic_threads, self->ident) < 0)
                PyErr_Clear();
        }
        Py_DECREF(self->ident);
        Py_INCREF(Py_None);
        self->ident = Py_None;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject*
PeriodicThread__before_fork(PeriodicThread* self, PyObject* Py_UNUSED(args))
{
    self->_skip_shutdown = true;

    // Synchronize with awake() so there is no window where _stopping is visible
    // before the fork-stop wake reason is published.
    {
        AllowThreads _(self->_state);
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
    if (self->_state != nullptr && self->_state->is_finalizing()) {
        // Do nothing. We are about to terminate and release resources anyway.
        return;
    }

    // DEV: With the current design, this dealloc can be triggered by the
    // periodic thread itself: PyRef (in the inner lambda scope) calls
    // Py_DECREF(self) while the GIL is still held by GILGuard. If refcount
    // hits zero, we arrive here from within the thread. This is safe because:
    //
    // 1. The GIL is held (GILGuard is still alive), so Py_XDECREF is safe.
    // 2. The thread is always detached at creation, so destroying _thread
    //    (non-joinable std::thread) is a no-op regardless of which thread
    //    calls it.
    // 3. After dealloc returns, the lambda only calls stopped_event->set()
    //    via its captured shared_ptr — it never accesses self again.
    // 4. GILGuard::~GILGuard (which runs after PyRef::~PyRef) only accesses
    //    its own _mstate copy, not self.
    //
    // Full cleanup is therefore correct in all cases;

    // Unmap the PeriodicThread from periodic_threads.
    if (self->ident != NULL && self->_state != nullptr && self->_state->periodic_threads != NULL &&
        PyDict_Contains(self->_state->periodic_threads, self->ident))
        PyDict_DelItem(self->_state->periodic_threads, self->ident);

    Py_XDECREF(self->name);
    Py_XDECREF(self->_target);
    Py_XDECREF(self->_on_shutdown);

    Py_XDECREF(self->ident);
    Py_XDECREF(self->_ddtrace_profiling_ignore);

    // Threads are always detached at creation, so joinable() is always false
    // and no OS call is needed. Just release the std::thread handle.
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
    { "_after_fork",
      (PyCFunction)(void*)PeriodicThread__after_fork,
      METH_VARARGS | METH_KEYWORDS,
      "Refresh the thread after fork" },
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
/**
 * Module-level atexit handler: sets the at_exit flag so all threads stop making
 * Python VM calls.
 */
static PyObject*
_threads_at_exit(PyObject* module, PyObject* Py_UNUSED(args))
{
    module_state* state = (module_state*)PyModule_GetState(module);

    // Stop and join all running periodic threads. We snapshot the values first
    // to avoid mutation of the dict during iteration (threads remove themselves
    // from periodic_threads when they stop).
    if (state != nullptr && state->periodic_threads != NULL) {
        PyObject* threads = PyDict_Values(state->periodic_threads);
        if (threads != NULL) {
            Py_ssize_t n = PyList_Size(threads);

            // Send the stop signal to all threads.
            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject* thread = PyList_GET_ITEM(threads, i);
                if (thread == NULL) {
                    PyErr_SetString(PyExc_RuntimeError, "Failed to get periodic thread from state");
                    break;
                }
                PyObject* result = PeriodicThread_stop((PeriodicThread*)thread, NULL);
                if (result == NULL)
                    PyErr_Clear();
                else
                    Py_DECREF(result);
            }

            // Join all threads.
            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject* thread = PyList_GET_ITEM(threads, i);
                if (thread == NULL) {
                    PyErr_SetString(PyExc_RuntimeError, "Failed to get periodic thread from state");
                    break;
                }
                PyObject* result = PeriodicThread_join((PeriodicThread*)thread, NULL, NULL);
                if (result == NULL)
                    PyErr_Clear();
                else
                    Py_DECREF(result);
            }

            Py_DECREF(threads);
        }
    }

    // Set the at_exit flag so that threads stop making Python VM calls. We do
    // this after we have stopped all threads to avoid potential deadlocks where
    // a thread has acquired the GIL but not released it because the VM shuts
    // down in the middle of its work.
    if (state != nullptr)
        state->at_exit.store(true, std::memory_order_release);

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static int
_threads_traverse(PyObject* module, visitproc visit, void* arg)
{
    module_state* state = (module_state*)PyModule_GetState(module);
    if (state != nullptr)
        Py_VISIT(state->periodic_threads);
    return 0;
}

// ----------------------------------------------------------------------------
static int
_threads_clear(PyObject* module)
{
    module_state* state = (module_state*)PyModule_GetState(module);
    if (state != nullptr)
        Py_CLEAR(state->periodic_threads);
    return 0;
}

// ----------------------------------------------------------------------------
static void
_threads_free(void* module)
{
    _threads_clear((PyObject*)module);
}

// ----------------------------------------------------------------------------
static PyMethodDef _threads_methods[] = {
    { "_at_exit", _threads_at_exit, METH_NOARGS, "Signal that Python is exiting" },
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static struct PyModuleDef threadsmodule = {
    PyModuleDef_HEAD_INIT,
    "_threads",           /* name of module */
    NULL,                 /* module documentation, may be NULL */
    sizeof(module_state), /* size of per-interpreter state of the module */
    _threads_methods,
    NULL,              /* m_slots */
    _threads_traverse, /* m_traverse */
    _threads_clear,    /* m_clear */
    _threads_free,     /* m_free */
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit__threads(void)
{
    PyObject* m = NULL;

    if (PyType_Ready(&PeriodicThreadType) < 0)
        return NULL;

    threadsmodule_ptr = &threadsmodule;
    m = PyModule_Create(threadsmodule_ptr);
    if (m == NULL)
        return NULL;

    // Initialize module state (placement new for std::atomic + PyObject* members).
    {
        module_state* state = (module_state*)PyModule_GetState(m);
        new (state) module_state();

        state->periodic_threads = PyDict_New();
        if (state->periodic_threads == NULL)
            goto error;

        // Register the atexit hook — the sole writer of module_state::at_exit.
        {
            PyObject* atexit_mod = PyImport_ImportModule("atexit");
            if (atexit_mod == NULL)
                goto error;
            PyObject* at_exit_fn = PyObject_GetAttrString(m, "_at_exit");
            if (at_exit_fn == NULL) {
                Py_DECREF(atexit_mod);
                goto error;
            }
            PyObject* res = PyObject_CallMethod(atexit_mod, "register", "O", at_exit_fn);
            Py_DECREF(at_exit_fn);
            Py_DECREF(atexit_mod);
            if (res == NULL)
                goto error;
            Py_DECREF(res);
        }
    }

    Py_INCREF(&PeriodicThreadType);
    if (PyModule_AddObject(m, "PeriodicThread", (PyObject*)&PeriodicThreadType) < 0) {
        Py_DECREF(&PeriodicThreadType);
        goto error;
    }

    {
        // PyModule_AddObject steals the reference on success; give it an extra
        // one so state->periodic_threads remains valid for the module's lifetime.
        module_state* state = (module_state*)PyModule_GetState(m);
        Py_INCREF(state->periodic_threads);
        if (PyModule_AddObject(m, "periodic_threads", state->periodic_threads) < 0) {
            Py_DECREF(state->periodic_threads);
            goto error;
        }
    }

    return m;

error:
    Py_XDECREF(m);

    return NULL;
}
