#include "cast_to_pyfunc.hpp"
#include "dd_wrapper/include/profiler_state.hpp"
#include "python_headers.hpp"
#include "sampler.hpp"
#include "thread_span_links.hpp"

#include "echion/echion_sampler.h"
#include "echion/vm.h"

using namespace Datadog;

static PyObject*
stack_start_impl(PyObject* self, PyObject* args, PyObject* kwargs)
{
    (void)self;
    static const char* const_kwlist[] = { "min_interval", NULL };
    static char** kwlist = const_cast<char**>(const_kwlist);
    double min_interval_s = g_default_sampling_period_s;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|d", kwlist, &min_interval_s)) {
        return NULL; // If an error occurs during argument parsing
    }

    Sampler::get().set_interval(min_interval_s);
    if (Sampler::get().start()) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

// Bypasses the old-style cast warning with an unchecked helper function
PyCFunction stack_start = cast_to_pycfunction(stack_start_impl);

static PyObject*
stack_stop(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(args))
{
    Sampler::get().stop();

    Py_BEGIN_ALLOW_THREADS; // Release GIL

    // Explicitly clear ThreadSpanLinks. The memory should be cleared up
    // when the program exits as ThreadSpanLinks is a static singleton instance.
    // However, this was necessary to make sure that the state is not shared
    // across tests, as the tests are run in the same process.
    ThreadSpanLinks::get_instance().reset();

    // Clear the native call registry. This is safe because we stop the
    // Sampler at the beginning of this function.
    ProfilerState::get().native_call_registry.reset();

    Py_END_ALLOW_THREADS; // Re-acquire GIL

    Py_RETURN_NONE;
}

static PyObject*
stack_set_interval(PyObject* self, PyObject* args)
{
    // Assumes the interval is given in fractional seconds
    (void)self;
    double new_interval;
    if (!PyArg_ParseTuple(args, "d", &new_interval)) {
        return NULL; // If an error occurs during argument parsing
    }
    Sampler::get().set_interval(new_interval);
    Py_RETURN_NONE;
}

// Echion needs us to propagate information about threads, usually at thread start by patching the threading module
// We reference some data structures here which are internal to echion (but global in scope)
static PyObject*
stack_thread_register(PyObject* self, PyObject* args)
{

    (void)self;

    uintptr_t id;
    uint64_t native_id;
    const char* name;

    if (!PyArg_ParseTuple(args, "KKs", &id, &native_id, &name)) {
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().register_thread(id, native_id, name);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
stack_thread_unregister(PyObject* self, PyObject* args)
{
    (void)self;
    uint64_t id;

    if (!PyArg_ParseTuple(args, "K", &id)) {
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().unregister_thread(id);
    ThreadSpanLinks::get_instance().unlink_span(id);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
stack_link_span_impl(PyObject* self, PyObject* args, PyObject* kwargs)
{
    (void)self;
    uint64_t thread_id;
    uint64_t span_id;
    uint64_t local_root_span_id;
    const char* span_type = nullptr;

    PyThreadState* state = PyThreadState_Get();

    if (!state) {
        return NULL;
    }

    thread_id = state->thread_id;

    static const char* const_kwlist[] = { "span_id", "local_root_span_id", "span_type", NULL };
    static char** kwlist = const_cast<char**>(const_kwlist);

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "KKz", kwlist, &span_id, &local_root_span_id, &span_type)) {
        return NULL;
    }

    // From Python, span_type is a string or None, and when given None, it is passed as a nullptr.
    static const std::string empty_string = "";
    if (span_type == nullptr) {
        span_type = empty_string.c_str();
    }

    Py_BEGIN_ALLOW_THREADS;
    ThreadSpanLinks::get_instance().link_span(thread_id, span_id, local_root_span_id, std::string(span_type));
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

PyCFunction stack_link_span = cast_to_pycfunction(stack_link_span_impl);

static PyObject*
stack_track_asyncio_loop(PyObject* self, PyObject* args)
{
    (void)self;
    uintptr_t thread_id; // map key
    PyObject* loop;

    if (!PyArg_ParseTuple(args, "lO", &thread_id, &loop)) {
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().track_asyncio_loop(thread_id, loop);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
stack_init_asyncio(PyObject* self, PyObject* args)
{
    (void)self;
    PyObject* asyncio_scheduled_tasks;
    PyObject* asyncio_eager_tasks;

    if (!PyArg_ParseTuple(args, "OO", &asyncio_scheduled_tasks, &asyncio_eager_tasks)) {
        return NULL;
    }

    Sampler::get().init_asyncio(asyncio_scheduled_tasks, asyncio_eager_tasks);

    Py_RETURN_NONE;
}

static PyObject*
stack_link_tasks(PyObject* self, PyObject* args)
{
    (void)self;
    PyObject *parent, *child;

    if (!PyArg_ParseTuple(args, "OO", &parent, &child)) {
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().link_tasks(parent, child);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
stack_weak_link_tasks(PyObject* self, PyObject* args)
{
    (void)self;
    PyObject *parent, *child;

    if (!PyArg_ParseTuple(args, "OO", &parent, &child)) {
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().weak_link_tasks(parent, child);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
stack_set_adaptive_sampling(PyObject* Py_UNUSED(self), PyObject* args)
{
    int do_adaptive_sampling = false;

    if (!PyArg_ParseTuple(args, "|p", &do_adaptive_sampling)) {
        return NULL;
    }

    Sampler::get().set_adaptive_sampling(do_adaptive_sampling);

    Py_RETURN_NONE;
}

static PyObject*
stack_set_target_overhead(PyObject* Py_UNUSED(self), PyObject* args)
{
    double target_overhead;

    if (!PyArg_ParseTuple(args, "d", &target_overhead)) {
        return NULL;
    }

    // Convert from percentage (0-100) to fraction (0-1)
    Sampler::get().set_target_overhead(target_overhead / 100.0);

    Py_RETURN_NONE;
}

static PyObject*
stack_set_max_sampling_period(PyObject* Py_UNUSED(self), PyObject* args)
{
    unsigned int max_interval_us;

    if (!PyArg_ParseTuple(args, "I", &max_interval_us)) {
        return NULL;
    }

    Sampler::get().set_max_sampling_period(max_interval_us);

    Py_RETURN_NONE;
}

static PyObject*
stack_set_max_threads(PyObject* Py_UNUSED(self), PyObject* args)
{
    unsigned int max_threads;

    if (!PyArg_ParseTuple(args, "I", &max_threads)) {
        return NULL;
    }

    Sampler::get().set_max_threads_per_sample(max_threads);

    Py_RETURN_NONE;
}

static PyObject*
stack_set_uvloop_mode(PyObject* Py_UNUSED(self), PyObject* args)
{
    uintptr_t thread_id;
    int uvloop_mode;

    if (!PyArg_ParseTuple(args, "lp", &thread_id, &uvloop_mode)) {
        return nullptr;
    }

    Sampler::get().set_uvloop_mode(thread_id, static_cast<bool>(uvloop_mode));

    Py_RETURN_NONE;
}

static PyObject*
track_greenlet(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id; // map key
    PyObject* name;
    PyObject* frame;

    if (!PyArg_ParseTuple(args, "lOO", &greenlet_id, &name, &frame))
        return NULL;

    auto& sampler = Sampler::get();
    auto maybe_greenlet_name = sampler.get_echion().string_table().key(name, StringTag::GreenletName);
    if (!maybe_greenlet_name) {
        // We failed to get this task but we keep going
        PyErr_SetString(PyExc_RuntimeError, "Failed to get greenlet name from the string table");
        return NULL;
    }

    auto greenlet_name = *maybe_greenlet_name;

    Py_BEGIN_ALLOW_THREADS;
    sampler.track_greenlet(greenlet_id, greenlet_name, frame);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
untrack_greenlet(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id;
    if (!PyArg_ParseTuple(args, "l", &greenlet_id))
        return NULL;

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().untrack_greenlet(greenlet_id);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
link_greenlets(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t parent, child;

    if (!PyArg_ParseTuple(args, "ll", &child, &parent))
        return NULL;

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().link_greenlets(parent, child);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

static PyObject*
update_greenlet_frame(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id;
    PyObject* frame;

    if (!PyArg_ParseTuple(args, "lO", &greenlet_id, &frame))
        return NULL;

    Py_BEGIN_ALLOW_THREADS;
    Sampler::get().update_greenlet_frame(greenlet_id, frame);
    Py_END_ALLOW_THREADS;

    Py_RETURN_NONE;
}

// ---- Native call monitoring (C callback for sys.monitoring CALL events) ----

// Cached sys.monitoring.DISABLE sentinel and tool ID (looked up at runtime)
static constexpr const char* g_tool_name = "dd-profiling";
static PyObject* g_disable_sentinel = nullptr;
static int g_tool_id = -1;

// C callback for sys.monitoring CALL events.
// On every CALL, extracts callable info, registers C callables in the registry,
// and returns DISABLE for all callables (Python and C) so each call site fires only once.
static PyObject*
native_call_handler(PyObject* Py_UNUSED(self), PyObject* const* args, Py_ssize_t nargs)
{
    // args: [code, instruction_offset, callable, arg0]
    if (nargs < 3) {
        Py_INCREF(g_disable_sentinel);
        return g_disable_sentinel;
    }

    PyObject* callable = args[2];

    // Exclude non-C callables: these are Python-level constructs whose
    // actual work (if any) will fire separate CALL events for the underlying
    // C functions they delegate to.
    // For types: heap types (Python-defined classes) are excluded because their
    // __init__/__new__ are Python functions visible as regular frames.
    // Non-heap types (builtins like str, dict, list, set) are kept because their
    // __init__/__new__ are C implementations that don't fire separate CALL events.
    if (PyFunction_Check(callable)  // def / lambda / async def
        || PyMethod_Check(callable) // bound method wrapping a PyFunction
        || (PyType_Check(callable) && (reinterpret_cast<PyTypeObject*>(callable)->tp_flags & Py_TPFLAGS_HEAPTYPE)) ||
        PyGen_Check(callable)              // generator object (not a C call)
        || PyCoro_CheckExact(callable)     // coroutine object
        || PyAsyncGen_CheckExact(callable) // async generator object
    ) {
        Py_INCREF(g_disable_sentinel);
        return g_disable_sentinel;
    }

    // Remaining callables are assumed to be C-level: builtin_function_or_method,
    // method_descriptor, slot_wrapper, classmethod_descriptor, etc.
    // Extract name+module, register call site, then return DISABLE.
    PyObject* code = args[0];
    PyObject* offset_obj = args[1];
    int offset_bytes = static_cast<int>(PyLong_AsLong(offset_obj));
    if (offset_bytes == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        Py_INCREF(g_disable_sentinel);
        return g_disable_sentinel;
    }

    uintptr_t code_ptr = reinterpret_cast<uintptr_t>(code);

    // Extract co_firstlineno to guard against code object address reuse after GC
    int first_lineno = reinterpret_cast<PyCodeObject*>(code)->co_firstlineno;

    // Get name: try __qualname__, fall back to __name__, then type name
    std::string name;
    PyObject* qualname = PyObject_GetAttrString(callable, "__qualname__");
    if (qualname && PyUnicode_Check(qualname)) {
        const char* s = PyUnicode_AsUTF8(qualname);
        if (s) {
            name = s;
        }
        Py_DECREF(qualname);
    } else {
        Py_XDECREF(qualname);
        PyErr_Clear();
        PyObject* pyname = PyObject_GetAttrString(callable, "__name__");
        if (pyname && PyUnicode_Check(pyname)) {
            const char* s = PyUnicode_AsUTF8(pyname);
            if (s) {
                name = s;
            }
            Py_DECREF(pyname);
        } else {
            Py_XDECREF(pyname);
            PyErr_Clear();
            name = Py_TYPE(callable)->tp_name;
        }
    }

    // Get module: try __module__, default ""
    std::string module;
    PyObject* pymod = PyObject_GetAttrString(callable, "__module__");
    if (pymod && PyUnicode_Check(pymod)) {
        const char* s = PyUnicode_AsUTF8(pymod);
        if (s) {
            module = s;
        }
        Py_DECREF(pymod);
    } else {
        Py_XDECREF(pymod);
        PyErr_Clear();
    }

    ProfilerState::get().native_call_registry.register_call_site(
      code_ptr, offset_bytes, first_lineno, std::move(name), std::move(module));

    Py_INCREF(g_disable_sentinel);
    return g_disable_sentinel;
}

static PyMethodDef native_call_handler_def = {
    "native_call_handler",
    // Double cast: METH_FASTCALL signature (PyObject*, PyObject*const*, Py_ssize_t) differs from
    // PyCFunction (PyObject*, PyObject*), but CPython dispatches correctly based on ml_flags.
    // NOLINTNEXTLINE(bugprone-casting-through-void)
    reinterpret_cast<PyCFunction>(reinterpret_cast<void*>(native_call_handler)),
    METH_FASTCALL,
    "C callback for sys.monitoring CALL events"
};

// Helper to clean up sys.monitoring state on error during start_native_monitoring.
// Unregisters events and frees the tool ID so a subsequent start attempt can succeed.
static void
cleanup_native_monitoring(PyObject* monitoring, bool events_set)
{
    if (events_set) {
        PyObject* r = PyObject_CallMethod(monitoring, "set_events", "ii", g_tool_id, 0);
        Py_XDECREF(r);
    }
    PyObject* r = PyObject_CallMethod(monitoring, "free_tool_id", "i", g_tool_id);
    Py_XDECREF(r);
}

static PyObject*
start_native_monitoring(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(args))
{
    // Import sys.monitoring
    PyObject* sys_mod = PyImport_ImportModule("sys");
    if (!sys_mod) {
        return NULL;
    }

    PyObject* monitoring = PyObject_GetAttrString(sys_mod, "monitoring");
    Py_DECREF(sys_mod);
    if (!monitoring) {
        return NULL;
    }

    // Cache the DISABLE sentinel
    if (!g_disable_sentinel) {
        g_disable_sentinel = PyObject_GetAttrString(monitoring, "DISABLE");
        if (!g_disable_sentinel) {
            Py_DECREF(monitoring);
            return NULL;
        }
    }

    // Look up PROFILER_ID at runtime instead of hardcoding
    if (g_tool_id < 0) {
        PyObject* id_obj = PyObject_GetAttrString(monitoring, "PROFILER_ID");
        if (!id_obj) {
            Py_DECREF(monitoring);
            return NULL;
        }
        g_tool_id = static_cast<int>(PyLong_AsLong(id_obj));
        Py_DECREF(id_obj);
        if (g_tool_id == -1 && PyErr_Occurred()) {
            Py_DECREF(monitoring);
            return NULL;
        }
    }

    // use_tool_id(g_tool_id, "dd-profiling")
    // If the tool ID is already claimed, check whether it's ours (idempotent
    // start) or belongs to another tool (raise RuntimeError).
    PyObject* result = PyObject_CallMethod(monitoring, "use_tool_id", "is", g_tool_id, g_tool_name);
    if (!result) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) {
            Py_DECREF(monitoring);
            return NULL;
        }
        PyErr_Clear();

        PyObject* current_name = PyObject_CallMethod(monitoring, "get_tool", "i", g_tool_id);
        if (!current_name) {
            Py_DECREF(monitoring);
            return NULL;
        }

        const char* name = PyUnicode_AsUTF8(current_name);
        bool is_ours = name && strcmp(name, g_tool_name) == 0;
        Py_DECREF(current_name);

        if (!is_ours) {
            Py_DECREF(monitoring);
            PyErr_SetString(PyExc_RuntimeError, "sys.monitoring PROFILER_ID is already claimed by another tool");
            return NULL;
        }
    } else {
        Py_DECREF(result);
    }

    // Get events.CALL
    PyObject* events = PyObject_GetAttrString(monitoring, "events");
    if (!events) {
        cleanup_native_monitoring(monitoring, false);
        Py_DECREF(monitoring);
        return NULL;
    }

    PyObject* call_event = PyObject_GetAttrString(events, "CALL");
    Py_DECREF(events);
    if (!call_event) {
        cleanup_native_monitoring(monitoring, false);
        Py_DECREF(monitoring);
        return NULL;
    }

    // set_events(g_tool_id, CALL)
    result = PyObject_CallMethod(monitoring, "set_events", "iO", g_tool_id, call_event);
    if (!result) {
        cleanup_native_monitoring(monitoring, false);
        Py_DECREF(call_event);
        Py_DECREF(monitoring);
        return NULL;
    }
    Py_DECREF(result);

    // restart_events() re-arms call sites that previously returned
    // sys.monitoring.DISABLE. Without this, a start->stop->start cycle would
    // leave already-seen call sites permanently disabled, and any new C call
    // sites sharing those code-object/offset pairs would never fire the
    // callback. This is a no-op on the first start (nothing is disabled yet).
    result = PyObject_CallMethod(monitoring, "restart_events", NULL);
    if (!result) {
        cleanup_native_monitoring(monitoring, true);
        Py_DECREF(call_event);
        Py_DECREF(monitoring);
        return NULL;
    }
    Py_DECREF(result);

    // Create the handler function object
    PyObject* handler = PyCFunction_New(&native_call_handler_def, NULL);
    if (!handler) {
        cleanup_native_monitoring(monitoring, true);
        Py_DECREF(call_event);
        Py_DECREF(monitoring);
        return NULL;
    }

    // register_callback(g_tool_id, CALL, handler)
    result = PyObject_CallMethod(monitoring, "register_callback", "iOO", g_tool_id, call_event, handler);
    Py_DECREF(handler);
    Py_DECREF(call_event);

    if (!result) {
        cleanup_native_monitoring(monitoring, true);
        Py_DECREF(monitoring);
        return NULL;
    }
    Py_DECREF(result);
    Py_DECREF(monitoring);

    Py_RETURN_NONE;
}

static PyObject*
stop_native_monitoring(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(args))
{
    if (g_tool_id < 0) {
        Py_RETURN_NONE;
    }

    PyObject* sys_mod = PyImport_ImportModule("sys");
    if (!sys_mod) {
        return NULL;
    }

    PyObject* monitoring = PyObject_GetAttrString(sys_mod, "monitoring");
    Py_DECREF(sys_mod);
    if (!monitoring) {
        return NULL;
    }

    // set_events(g_tool_id, 0) - disable all events
    PyObject* result = PyObject_CallMethod(monitoring, "set_events", "ii", g_tool_id, 0);
    if (!result) {
        Py_DECREF(monitoring);
        return NULL;
    }
    Py_DECREF(result);

    // Get events.CALL for unregistering
    PyObject* events = PyObject_GetAttrString(monitoring, "events");
    if (!events) {
        Py_DECREF(monitoring);
        return NULL;
    }

    PyObject* call_event = PyObject_GetAttrString(events, "CALL");
    Py_DECREF(events);
    if (!call_event) {
        Py_DECREF(monitoring);
        return NULL;
    }

    // register_callback(g_tool_id, CALL, None)
    result = PyObject_CallMethod(monitoring, "register_callback", "iOO", g_tool_id, call_event, Py_None);
    Py_DECREF(call_event);
    if (!result) {
        Py_DECREF(monitoring);
        return NULL;
    }
    Py_DECREF(result);

    // free_tool_id(g_tool_id)
    result = PyObject_CallMethod(monitoring, "free_tool_id", "i", g_tool_id);
    Py_DECREF(monitoring);
    if (!result) {
        return NULL;
    }
    Py_DECREF(result);
    g_tool_id = -1;

    Py_CLEAR(g_disable_sentinel);

    Py_RETURN_NONE;
}

static PyObject*
stack_set_fast_copy(PyObject* Py_UNUSED(self), PyObject* args)
{
    int enabled = true;

    if (!PyArg_ParseTuple(args, "|p", &enabled)) {
        return NULL;
    }

    set_fast_copy_enabled(static_cast<bool>(enabled));

    Py_RETURN_NONE;
}

static PyObject*
stack_is_safe_copy_failed(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(args))
{
// process_vm_readv is always available on macOS
#if defined PL_LINUX
    if (failed_safe_copy) {
        Py_RETURN_TRUE;
    }
#endif
    Py_RETURN_FALSE;
}

static PyMethodDef stack_methods[] = {
    { "start", reinterpret_cast<PyCFunction>(stack_start), METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "stop", stack_stop, METH_VARARGS, "Stop the sampler" },
    { "register_thread", stack_thread_register, METH_VARARGS, "Register a thread" },
    { "unregister_thread", stack_thread_unregister, METH_VARARGS, "Unregister a thread" },
    { "set_interval", stack_set_interval, METH_VARARGS, "Set the sampling interval" },
    { "link_span",
      reinterpret_cast<PyCFunction>(stack_link_span),
      METH_VARARGS | METH_KEYWORDS,
      "Link a span to a thread" },
    // asyncio task support
    { "track_asyncio_loop", stack_track_asyncio_loop, METH_VARARGS, "Map the name of a task with its identifier" },
    { "init_asyncio", stack_init_asyncio, METH_VARARGS, "Initialise asyncio tracking" },
    { "link_tasks", stack_link_tasks, METH_VARARGS, "Link two tasks" },
    { "weak_link_tasks", stack_weak_link_tasks, METH_VARARGS, "Weakly link two tasks" },
    // greenlet support
    { "track_greenlet", track_greenlet, METH_VARARGS, "Map a greenlet with its identifier" },
    { "untrack_greenlet", untrack_greenlet, METH_VARARGS, "Untrack a terminated greenlet" },
    { "link_greenlets", link_greenlets, METH_VARARGS, "Link two greenlets" },
    { "update_greenlet_frame", update_greenlet_frame, METH_VARARGS, "Update the frame of a greenlet" },

    { "set_adaptive_sampling", stack_set_adaptive_sampling, METH_VARARGS, "Set adaptive sampling" },
    { "set_target_overhead",
      stack_set_target_overhead,
      METH_VARARGS,
      "Set target overhead for adaptive sampling (e.g. 10 for 10% of the app's own CPU time)" },
    { "set_max_sampling_period",
      stack_set_max_sampling_period,
      METH_VARARGS,
      "Set max sampling period for adaptive sampling" },
    { "set_max_threads", stack_set_max_threads, METH_VARARGS, "Set max threads to sample per cycle (0 = unlimited)" },
    { "set_uvloop_mode", stack_set_uvloop_mode, METH_VARARGS, "Enable uvloop-specific stack unwinding for a thread" },
    // Memory copy strategy
    { "set_fast_copy", stack_set_fast_copy, METH_VARARGS, "Enable or disable fast memory copying (safe_memcpy)" },
    { "is_safe_copy_failed",
      stack_is_safe_copy_failed,
      METH_NOARGS,
      "Check if all safe copy methods failed to initialize" },
    // Native call monitoring
    { "start_native_monitoring",
      start_native_monitoring,
      METH_NOARGS,
      "Start sys.monitoring-based native call tracking" },
    { "stop_native_monitoring", stop_native_monitoring, METH_NOARGS, "Stop sys.monitoring-based native call tracking" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC
PyInit__stack(void) // NOLINT(bugprone-reserved-identifier)
{
    PyObject* m;
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT, "_stack", NULL, -1, stack_methods, NULL, NULL, NULL, NULL
    };

    m = PyModule_Create(&moduledef);
    if (!m)
        return NULL;

    return m;
}
