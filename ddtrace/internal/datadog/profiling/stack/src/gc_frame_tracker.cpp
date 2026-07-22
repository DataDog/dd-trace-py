#define Py_BUILD_CORE
#include "gc_frame_tracker.hpp"

#include "echion/vm.h"

#if PY_VERSION_HEX >= 0x030f0000
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#include <internal/pycore_interp.h>
#endif

#include <cstddef>
#include <new>

namespace Datadog {

#if PY_VERSION_HEX < 0x030f0000

namespace {

PyObject*
current_python_frame(PyThreadState* tstate)
{
#if PY_VERSION_HEX >= 0x030d0000
    return reinterpret_cast<PyObject*>(tstate->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    return tstate->cframe == nullptr ? nullptr : reinterpret_cast<PyObject*>(tstate->cframe->current_frame);
#else
    return reinterpret_cast<PyObject*>(tstate->frame);
#endif
}

PyObject*
gc_callback(PyObject* Py_UNUSED(self), PyObject* args)
{
    PyObject* phase = nullptr;
    PyObject* info = nullptr;
    if (!PyArg_ParseTuple(args, "OO", &phase, &info)) {
        return nullptr;
    }
    (void)info;

    if (!PyUnicode_Check(phase)) {
        Py_RETURN_NONE;
    }

    bool is_start = PyUnicode_CompareWithASCIIString(phase, "start") == 0;
    bool is_stop = !is_start && PyUnicode_CompareWithASCIIString(phase, "stop") == 0;
    if (PyErr_Occurred()) {
        PyErr_Clear();
        Py_RETURN_NONE;
    }
    if (!is_start && !is_stop) {
        Py_RETURN_NONE;
    }

    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == nullptr) {
        Py_RETURN_NONE;
    }

    auto& tracker = GCFrameTracker::get();
    // The callback itself is a native callable and does not add a Python frame,
    // so the thread state's current frame is the frame that initiated GC.
    PyObject* frame = is_start ? current_python_frame(tstate) : nullptr;
    tracker.update(tstate->interp, frame);

    Py_RETURN_NONE;
}

PyMethodDef gc_callback_def = { "_ddtrace_gc_callback",
                                gc_callback,
                                METH_VARARGS,
                                "Track the Python frame that initiated garbage collection" };

PyObject*
get_gc_callbacks()
{
    PyObject* gc_module = PyImport_ImportModule("gc");
    if (gc_module == nullptr) {
        return nullptr;
    }

    PyObject* callbacks = PyObject_GetAttrString(gc_module, "callbacks");
    Py_DECREF(gc_module);
    if (callbacks == nullptr) {
        return nullptr;
    }
    if (!PyList_Check(callbacks)) {
        Py_DECREF(callbacks);
        PyErr_SetString(PyExc_RuntimeError, "gc.callbacks is not a list");
        return nullptr;
    }
    return callbacks;
}

} // namespace

#endif // PY_VERSION_HEX < 0x030f0000

GCFrameTracker&
GCFrameTracker::get()
{
    static GCFrameTracker tracker;
    return tracker;
}

bool
GCFrameTracker::install_current_interpreter()
{
#if PY_VERSION_HEX >= 0x030f0000
    return true;
#else
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "cannot install GC tracking without a thread state");
        return false;
    }

    PyObject* callbacks = get_gc_callbacks();
    if (callbacks == nullptr) {
        return false;
    }

    PyObject* candidate = PyCFunction_New(&gc_callback_def, nullptr);
    if (candidate == nullptr) {
        Py_DECREF(callbacks);
        return false;
    }

    PyObject* callback = nullptr;
    bool created = false;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto [state, inserted] = states_.try_emplace(tstate->interp);
        (void)inserted;
        if (state->second.callback == nullptr) {
            state->second.callback = candidate;
            callback = candidate;
            created = true;
        } else {
            callback = state->second.callback;
        }
        Py_INCREF(callback); // local reference used outside the lock
    }
    if (!created) {
        Py_DECREF(candidate);
    }

    bool registered = false;
    int append_result = 0;
#if PY_VERSION_HEX >= 0x030d0000
    // gc.callbacks can be mutated by another thread on free-threaded builds.
    // Hold the list's critical section across the identity scan and append so
    // concurrent starts cannot register the same callback twice.
    Py_BEGIN_CRITICAL_SECTION(callbacks);
#endif
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(callbacks); ++i) {
        if (PyList_GET_ITEM(callbacks, i) == callback) {
            registered = true;
            break;
        }
    }
    if (!registered) {
        append_result = PyList_Append(callbacks, callback);
    }
#if PY_VERSION_HEX >= 0x030d0000
    Py_END_CRITICAL_SECTION();
#endif

    if (append_result < 0) {
        if (created) {
            PyObject* owned_callback = nullptr;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                auto state = states_.find(tstate->interp);
                if (state != states_.end() && state->second.callback == callback) {
                    owned_callback = state->second.callback;
                    states_.erase(state);
                }
            }
            Py_XDECREF(owned_callback);
        }
        Py_DECREF(callback);
        Py_DECREF(callbacks);
        return false;
    }

    Py_DECREF(callback);
    Py_DECREF(callbacks);
    return true;
#endif
}

bool
GCFrameTracker::uninstall_current_interpreter()
{
#if PY_VERSION_HEX >= 0x030f0000
    return true;
#else
    PyThreadState* tstate = PyThreadState_Get();
    if (tstate == nullptr) {
        PyErr_SetString(PyExc_RuntimeError, "cannot uninstall GC tracking without a thread state");
        return false;
    }

    PyObject* callback = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto state = states_.find(tstate->interp);
        if (state == states_.end() || state->second.callback == nullptr) {
            return true;
        }
        callback = state->second.callback;
        Py_INCREF(callback);
    }

    PyObject* callbacks = get_gc_callbacks();
    if (callbacks == nullptr) {
        Py_DECREF(callback);
        return false;
    }

    // Delete by identity, not equality, and remove every accidental duplicate.
    // User callbacks are never invoked or modified by this cleanup. The list
    // critical section makes the identity scan safe on free-threaded builds.
    bool removal_failed = false;
#if PY_VERSION_HEX >= 0x030d0000
    Py_BEGIN_CRITICAL_SECTION(callbacks);
#endif
    for (Py_ssize_t i = PyList_GET_SIZE(callbacks); i-- > 0;) {
        if (PyList_GET_ITEM(callbacks, i) == callback && PySequence_DelItem(callbacks, i) < 0) {
            removal_failed = true;
            break;
        }
    }
#if PY_VERSION_HEX >= 0x030d0000
    Py_END_CRITICAL_SECTION();
#endif
    if (removal_failed) {
        Py_DECREF(callbacks);
        Py_DECREF(callback);
        return false;
    }
    Py_DECREF(callbacks);

    PyObject* owned_callback = nullptr;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto state = states_.find(tstate->interp);
        if (state != states_.end() && state->second.callback == callback) {
            owned_callback = state->second.callback;
            states_.erase(state);
        }
    }
    Py_XDECREF(owned_callback);
    Py_DECREF(callback);
    return true;
#endif
}

PyObject*
GCFrameTracker::capture(PyInterpreterState* interp)
{
#if PY_VERSION_HEX >= 0x030f0000
    _PyInterpreterFrame* frame = nullptr;
    auto* frame_addr = reinterpret_cast<char*>(interp) + offsetof(PyInterpreterState, gc.frame);
    if (copy_type(frame_addr, frame)) {
        return nullptr;
    }
    return reinterpret_cast<PyObject*>(frame);
#else
    // AIDEV-NOTE: Before CPython 3.15, GC-frame coverage is limited to interpreters
    // where dd-trace-py installed its native gc.callbacks callback.
    std::lock_guard<std::mutex> lock(mutex_);
    auto state = states_.find(interp);
    return state == states_.end() ? nullptr : state->second.frame;
#endif
}

#if PY_VERSION_HEX < 0x030f0000
void
GCFrameTracker::update(PyInterpreterState* interp, PyObject* frame)
{
    std::lock_guard<std::mutex> lock(mutex_);
    auto state = states_.find(interp);
    if (state != states_.end()) {
        state->second.frame = frame;
    }
}
#endif

void
GCFrameTracker::prefork()
{
#if PY_VERSION_HEX < 0x030f0000
    mutex_.lock();
#endif
}

void
GCFrameTracker::postfork_parent()
{
#if PY_VERSION_HEX < 0x030f0000
    mutex_.unlock();
#endif
}

void
GCFrameTracker::postfork_child()
{
#if PY_VERSION_HEX < 0x030f0000
    // prepare() made the map stable before fork. The inherited callback objects
    // remain registered and owned by their interpreters, but no child may use a
    // frame pointer captured in the parent.
    for (auto& [interp, state] : states_) {
        (void)interp;
        state.frame = nullptr;
    }
    new (&mutex_) std::mutex;
#endif
}

} // namespace Datadog
