// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif

#include <condition_variable>
#include <fstream>
#include <iostream>
#include <mutex>
#include <thread>

#include <fcntl.h>
#include <sched.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#if defined PL_DARWIN
#include <pthread.h>
#endif

#include <echion/config.h>
#include <echion/greenlets.h>
#include <echion/interp.h>
#include <echion/memory.h>
#include <echion/mojo.h>
#include <echion/signals.h>
#include <echion/stacks.h>
#include <echion/state.h>
#include <echion/threads.h>
#include <echion/timing.h>

// ----------------------------------------------------------------------------
static void do_where(std::ostream& stream)
{
    WhereRenderer::get().set_output(stream);
    WhereRenderer::get().render_message("\r🐴 Echion reporting for duty");
    WhereRenderer::get().render_message("");

    for_each_interp([](InterpreterInfo& interp) -> void {
        for_each_thread(interp, [](PyThreadState* tstate, ThreadInfo& thread) -> void {
            thread.unwind(tstate);
            WhereRenderer::get().render_thread_begin(tstate, thread.name, /*cpu_time*/ 0,
                                                     tstate->thread_id, thread.native_id);

            if (native)
            {
                auto interleave_success = interleave_stacks();
                if (!interleave_success) {
                    std::cerr << "could not interleave stacks" << std::endl;
                    return;
                }

                interleaved_stack.render_where();
            }
            else
                python_stack.render_where();
            WhereRenderer::get().render_message("");
        });
    });
}

// ----------------------------------------------------------------------------
static void where_listener()
{
    for (;;)
    {
        std::unique_lock<std::mutex> lock(where_lock);
        where_cv.wait(lock);

        if (!running)
            break;

        do_where(std::cerr);
    }
}

// ----------------------------------------------------------------------------
static void setup_where()
{
    where_thread = new std::thread(where_listener);
}

static void teardown_where()
{
    if (where_thread != nullptr)
    {
        {
            std::lock_guard<std::mutex> lock(where_lock);

            where_cv.notify_one();
        }

        where_thread->join();

        where_thread = nullptr;
    }
}

// ----------------------------------------------------------------------------
static inline void _start()
{
    init_frame_cache(CACHE_MAX_ENTRIES * (1 + native));

    auto open_success = Renderer::get().open();
    if (!open_success) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to open renderer");
        return;
    }

    install_signals();

#if defined PL_DARWIN
    // Get the wall time clock resource.
    host_get_clock_service(mach_host_self(), CALENDAR_CLOCK, &cclock);
#endif

    if (where)
    {
        std::ofstream pipe(pipe_name, std::ios::out);

        if (pipe)
            do_where(pipe);

        else
            std::cerr << "Failed to open pipe " << pipe_name << std::endl;

        running = 0;

        return;
    }

    setup_where();

    Renderer::get().header();

    if (memory)
    {
        Renderer::get().metadata("mode", "memory");
    }
    else
    {
        Renderer::get().metadata("mode", (cpu ? "cpu" : "wall"));
    }
    Renderer::get().metadata("interval", std::to_string(interval));
    Renderer::get().metadata("sampler", "echion");

    // DEV: Workaround for the austin-python library: we send an empty sample
    // to set the PID. We also map the key value 0 to the empty string, to
    // support task name frames.
    Renderer::get().render_stack_begin(pid, 0, "MainThread");
    Renderer::get().string(0, "");
    Renderer::get().string(1, "<invalid>");
    Renderer::get().string(2, "<unknown>");
    Renderer::get().render_stack_end(MetricType::Time, 0);

    if (memory)
        setup_memory();
}

// ----------------------------------------------------------------------------
static inline void _stop()
{
    if (memory)
        teardown_memory();

    // Clean up the thread info map. When not running async, we need to guard
    // the map lock because we are not in control of the sampling thread.
    {
        const std::lock_guard<std::mutex> guard(thread_info_map_lock);

        thread_info_map.clear();
        string_table.clear();
    }

    teardown_where();

#if defined PL_DARWIN
    mach_port_deallocate(mach_task_self(), cclock);
#endif

    restore_signals();

    Renderer::get().close();

    reset_frame_cache();
}

// ----------------------------------------------------------------------------
static inline void _sampler()
{
    // This function can run without the GIL on the basis that these assumptions
    // hold:
    // 1. The interpreter state object lives as long as the process itself.

    last_time = gettime();

    while (running)
    {
        microsecond_t now = gettime();
        microsecond_t end_time = now + interval;

        if (memory)
        {
            if (rss_tracker.check())
                stack_stats.flush();
        }
        else
        {
            microsecond_t wall_time = now - last_time;

            for_each_interp([=](InterpreterInfo& interp) -> void {
                for_each_thread(interp, [=](PyThreadState* tstate, ThreadInfo& thread) {
                    auto sample_success = thread.sample(interp.id, tstate, wall_time);
                    if (!sample_success) {
                        // Silently skip sampling this thread
                    }
                });
            });
        }

        std::this_thread::sleep_for(std::chrono::microseconds(end_time - now));
        last_time = now;
    }
}

static void sampler()
{
    _start();
    _sampler();
    _stop();
}

// ----------------------------------------------------------------------------
static void _init()
{
    pid = getpid();
}

// ----------------------------------------------------------------------------
static PyObject* start_async(PyObject* Py_UNUSED(m), PyObject* Py_UNUSED(args))
{
    if (!running)
    {
        // TODO: Since we have a global state, we should not allow multiple ways
        // of starting the sampler.
        if (sampler_thread == nullptr)
        {
            running = 1;
            sampler_thread = new std::thread(sampler);
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* start(PyObject* Py_UNUSED(m), PyObject* Py_UNUSED(args))
{
    if (!running)
    {
        // TODO: Since we have a global state, we should not allow multiple ways
        // of starting the sampler.
        running = 1;

        // Run the sampler without the GIL
        Py_BEGIN_ALLOW_THREADS;
        sampler();
        Py_END_ALLOW_THREADS;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* stop(PyObject* Py_UNUSED(m), PyObject* Py_UNUSED(args))
{
    running = 0;

    // Stop the sampling thread
    if (sampler_thread != nullptr)
    {
        sampler_thread->join();
        sampler_thread = nullptr;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* track_thread(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t thread_id;  // map key
    const char* thread_name;
    pid_t native_id;

    if (!PyArg_ParseTuple(args, "lsi", &thread_id, &thread_name, &native_id))
        return NULL;

    {
        const std::lock_guard<std::mutex> guard(thread_info_map_lock);

        auto maybe_thread_info = ThreadInfo::create(thread_id, native_id, thread_name);
        if (!maybe_thread_info) {
            PyErr_SetString(PyExc_RuntimeError, "Failed to track thread");
            return nullptr;
        }

        auto entry = thread_info_map.find(thread_id);
        if (entry != thread_info_map.end()) {
            // Thread is already tracked so we update its info
            entry->second = std::move(*maybe_thread_info);
        } else {
            thread_info_map.emplace(thread_id, std::move(*maybe_thread_info));
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* untrack_thread(PyObject* Py_UNUSED(m), PyObject* args)
{
    unsigned long thread_id;
    if (!PyArg_ParseTuple(args, "l", &thread_id))
        return NULL;

    {
        const std::lock_guard<std::mutex> guard(thread_info_map_lock);

        thread_info_map.erase(thread_id);
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* init(PyObject* Py_UNUSED(m), PyObject* Py_UNUSED(args))
{
    _init();

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* track_asyncio_loop(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t thread_id;  // map key
    PyObject* loop;

    if (!PyArg_ParseTuple(args, "lO", &thread_id, &loop))
        return NULL;

    {
        std::lock_guard<std::mutex> guard(thread_info_map_lock);

        if (thread_info_map.find(thread_id) != thread_info_map.end())
        {
            thread_info_map.find(thread_id)->second->asyncio_loop =
                (loop != Py_None) ? (uintptr_t)loop : 0;
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* init_asyncio(PyObject* Py_UNUSED(m), PyObject* args)
{
    if (!PyArg_ParseTuple(args, "OOO", &asyncio_current_tasks, &asyncio_scheduled_tasks,
                          &asyncio_eager_tasks))
        return NULL;

    if (asyncio_eager_tasks == Py_None)
        asyncio_eager_tasks = NULL;

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* track_greenlet(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id;  // map key
    PyObject* name;
    PyObject* frame;

    if (!PyArg_ParseTuple(args, "lOO", &greenlet_id, &name, &frame))
        return NULL;

    StringTable::Key greenlet_name;

    auto maybe_greenlet_name = string_table.key(name);
    if (!maybe_greenlet_name)
    {
        // We failed to get this task but we keep going
        PyErr_SetString(PyExc_RuntimeError, "Failed to get greenlet name from the string table");
        return NULL;
    }
    greenlet_name = *maybe_greenlet_name;

    {
        const std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

        auto entry = greenlet_info_map.find(greenlet_id);
        if (entry != greenlet_info_map.end())
            // Greenlet is already tracked so we update its info. This should
            // never happen, as a greenlet should be tracked only once, so we
            // use this as a safety net.
            entry->second = std::make_unique<GreenletInfo>(greenlet_id, frame, greenlet_name);
        else
            greenlet_info_map.emplace(
                greenlet_id, std::make_unique<GreenletInfo>(greenlet_id, frame, greenlet_name));

        // Update the thread map
        auto native_id = PyThread_get_thread_native_id();
        greenlet_thread_map[native_id] = greenlet_id;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* untrack_greenlet(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id;
    if (!PyArg_ParseTuple(args, "l", &greenlet_id))
        return NULL;

    {
        const std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

        greenlet_info_map.erase(greenlet_id);
        greenlet_parent_map.erase(greenlet_id);
        greenlet_thread_map.erase(greenlet_id);
    }
    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* link_greenlets(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t parent, child;

    if (!PyArg_ParseTuple(args, "ll", &child, &parent))
        return NULL;

    {
        std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

        greenlet_parent_map[child] = parent;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* update_greenlet_frame(PyObject* Py_UNUSED(m), PyObject* args)
{
    uintptr_t greenlet_id;
    PyObject* frame;

    if (!PyArg_ParseTuple(args, "lO", &greenlet_id, &frame))
        return NULL;

    {
        std::lock_guard<std::mutex> guard(greenlet_info_map_lock);

        auto entry = greenlet_info_map.find(greenlet_id);
        if (entry != greenlet_info_map.end())
        {
            // Update the frame of the greenlet
            entry->second->frame = frame;
        }
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyObject* link_tasks(PyObject* Py_UNUSED(m), PyObject* args)
{
    PyObject *parent, *child;

    if (!PyArg_ParseTuple(args, "OO", &parent, &child))
        return NULL;

    {
        std::lock_guard<std::mutex> guard(task_link_map_lock);

        task_link_map[child] = parent;
    }

    Py_RETURN_NONE;
}

// ----------------------------------------------------------------------------
static PyMethodDef echion_core_methods[] = {
    {"start", start, METH_NOARGS, "Start the stack sampler"},
    {"start_async", start_async, METH_NOARGS, "Start the stack sampler asynchronously"},
    {"stop", stop, METH_NOARGS, "Stop the stack sampler"},
    {"track_thread", track_thread, METH_VARARGS, "Map the name of a thread with its identifier"},
    {"untrack_thread", untrack_thread, METH_VARARGS, "Untrack a terminated thread"},
    {"init", init, METH_NOARGS, "Initialize the stack sampler (usually after a fork)"},
    // Task support
    {"track_asyncio_loop", track_asyncio_loop, METH_VARARGS,
     "Map the name of a task with its identifier"},
    {"init_asyncio", init_asyncio, METH_VARARGS, "Initialise asyncio tracking"},
    {"link_tasks", link_tasks, METH_VARARGS, "Link two tasks"},
    // Greenlet support
    {"track_greenlet", track_greenlet, METH_VARARGS, "Map a greenlet with its identifier"},
    {"untrack_greenlet", untrack_greenlet, METH_VARARGS, "Untrack a terminated greenlet"},
    {"link_greenlets", link_greenlets, METH_VARARGS, "Link two greenlets"},
    {"update_greenlet_frame", update_greenlet_frame, METH_VARARGS,
     "Update the frame of a greenlet"},
    // Configuration interface
    {"set_interval", set_interval, METH_VARARGS, "Set the sampling interval"},
    {"set_cpu", set_cpu, METH_VARARGS, "Set whether to use CPU time instead of wall time"},
    {"set_memory", set_memory, METH_VARARGS, "Set whether to sample memory usage"},
    {"set_native", set_native, METH_VARARGS, "Set whether to sample the native stacks"},
    {"set_where", set_where, METH_VARARGS, "Set whether to use where mode"},
    {"set_pipe_name", set_pipe_name, METH_VARARGS, "Set the pipe name"},
    {"set_max_frames", set_max_frames, METH_VARARGS, "Set the max number of frames to unwind"},
    // Sentinel
    {NULL, NULL, 0, NULL}
};

// ----------------------------------------------------------------------------
static struct PyModuleDef coremodule = {
    PyModuleDef_HEAD_INIT,
    "core", /* name of module */
    NULL,   /* module documentation, may be NULL */
    -1,     /* size of per-interpreter state of the module,
               or -1 if the module keeps state in global variables. */
    echion_core_methods,
    nullptr,   /* m_traverse */
    nullptr,   /* m_clear */
    nullptr,   /* m_free */
    nullptr,   /* m_is_preinitialised */
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC PyInit_core(void)
{
    PyObject* m;

    m = PyModule_Create(&coremodule);
    if (m == NULL)
        return NULL;

    // We make the assumption that this module is loaded by the main thread.
    // TODO: These need to be reset after a fork.
    _init();

    return m;
}
