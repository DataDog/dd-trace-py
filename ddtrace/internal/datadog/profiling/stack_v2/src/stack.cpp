#define PY_SSIZE_T_CLEAN
#include <Python.h>
#
#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif

#include <atomic>
#include <chrono>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

#include "echion/config.h"
#include "echion/interp.h"
#include "echion/signals.h"
#include "echion/stacks.h"
#include "echion/state.h"
#include "echion/threads.h"
#include "echion/timing.h"

#include "interface.hpp"

// From https://gist.github.com/ichramm/3ffeaf7ba4f24853e9ecaf176da84566
// TODO find a better thing and credit it
bool
utf8_check_is_valid(const char* str, int len)
{
    int n;
    for (int i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)str[i];
        // if (c==0x09 || c==0x0a || c==0x0d || (0x20 <= c && c <= 0x7e) ) n = 0; // is_printable_ascii
        if (0x00 <= c && c <= 0x7f) {
            n = 0; // 0bbbbbbb
        } else if ((c & 0xE0) == 0xC0) {
            n = 1; // 110bbbbb
        } else if (c == 0xed && i < (len - 1) && ((unsigned char)str[i + 1] & 0xa0) == 0xa0) {
            return false; // U+d800 to U+dfff
        } else if ((c & 0xF0) == 0xE0) {
            n = 2; // 1110bbbb
        } else if ((c & 0xF8) == 0xF0) {
            n = 3; // 11110bbb
            //} else if (($c & 0xFC) == 0xF8) { n=4; // 111110bb //byte 5, unnecessary in 4 byte UTF-8
            //} else if (($c & 0xFE) == 0xFC) { n=5; // 1111110b //byte 6, unnecessary in 4 byte UTF-8
        } else {
            return false;
        }

        for (int j = 0; j < n && i < len; ++j) { // n bytes matching 10bbbbbb follow ?
            if ((++i == len) || (((unsigned char)str[i] & 0xC0) != 0x80)) {
                return false;
            }
        }
    }
    return true;
}

class StackRenderer : public RendererInterface
{
    static PyObject* stack_sample_event_type;
    static PyObject* ddframe_type;
    void render_message(std::string_view msg) override { (void)msg; }

    void render_thread_begin(PyThreadState* tstate,
                             std::string_view name,
                             microsecond_t wall_time,
                             uintptr_t thread_id,
                             unsigned long native_id) override
    {
        ddup_start_sample();
        ddup_push_threadinfo(static_cast<int64_t>(thread_id), static_cast<int64_t>(native_id), name.data());
        ddup_push_walltime(1000 * wall_time, 1);
    }

    void render_stack_begin() override
    {
        // Whatever, this is definitely a weird hack for now
    }

    void render_python_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        static const std::string_view invalid = "<invalid_utf8>";
        if (!utf8_check_is_valid(name.data(), name.size())) {
            name = invalid;
        }
        if (!utf8_check_is_valid(file.data(), file.size())) {
            file = invalid;
        }
        ddup_push_frame(name.data(), file.data(), 0, line);
    }

    void render_native_frame(std::string_view name, std::string_view file, uint64_t line) override
    {
        ddup_push_frame(name.data(), file.data(), 0, line);
    }

    void render_cpu_time(uint64_t cpu_time) override { ddup_push_cputime(1000 * cpu_time, 1); }

    void render_stack_end() override { ddup_flush_sample(); }

    bool is_valid() override { return true; }

  public:
    void set_type()
    {
        PyObject* mod_name = PyUnicode_FromString("ddtrace.profiling.event");
        PyObject* mod = PyImport_Import(mod_name);
        Py_XDECREF(mod_name);
        if (mod == NULL) {
            PyErr_Print();
            exit(1);
        }
        ddframe_type = PyObject_GetAttrString(mod, "DDFrame");
        Py_XDECREF(mod);

        mod_name = PyUnicode_FromString("ddtrace.profiling.collector.stack_event");
        mod = PyImport_Import(mod_name);
        Py_XDECREF(mod_name);
        if (mod == NULL) {
            PyErr_Print();
            exit(1);
        }
        stack_sample_event_type = PyObject_GetAttrString(mod, "StackSampleEvent");
        Py_XDECREF(mod);

        // Check for errors
        if (stack_sample_event_type == NULL) {
            PyErr_Print();
            exit(1);
        }
        if (ddframe_type == NULL) {
            PyErr_Print();
            exit(1);
        }
    }
};

// Initialize static members
PyObject* StackRenderer::stack_sample_event_type = nullptr;
PyObject* StackRenderer::ddframe_type = nullptr;

// Switch for stopping the profiler
std::atomic<bool> _stop_stack_v2(false);

// Accepts fractional seconds, saves variable as integral us
std::atomic<unsigned long> sample_interval = 10000; // in us
static void
_set_v2_interval(double new_interval)
{
    unsigned int new_interval_us = static_cast<unsigned int>(new_interval * 1e6);
    sample_interval.store(new_interval_us);
}

void
_stack_sampler_v2()
{
    using namespace std::chrono;
    unsigned long samples = 0;
    auto last_time = steady_clock::now();

    while (true) {
        auto now = steady_clock::now();
        auto wall_time = duration_cast<microseconds>(now - last_time).count();
        last_time = now;

        // Perform the sample
        int num_threads = 0;
        int num_interps = 0;
        for_each_interp([&](PyInterpreterState* interp) -> void {
            num_interps++;
            for_each_thread(interp, [&](PyThreadState* tstate, ThreadInfo& thread) {
                num_threads++;
                thread.sample(interp->id, tstate, wall_time);
            });
        });

        // If we've been asked to stop, then stop
        if (_stop_stack_v2.load()) {
            break;
        }

        // Sleep for the remainder of the interval, get it atomically
        std::this_thread::sleep_until(now + microseconds(sample_interval.load()));
    }

    // Reset the stop flag
    _stop_stack_v2.store(false);
}

void
stack_sampler_v2()
{
    // TODO lifetime?
    std::thread(_stack_sampler_v2).detach();
}

void
make_it_abort()
{
    std::abort();
}

// All interfaces use the same global instance of the renderer, since
// it keeps persistent state
std::shared_ptr<StackRenderer> _renderer = std::make_shared<StackRenderer>();

static PyObject*
start_stack_v2(PyObject* self, PyObject* args, PyObject* kwargs)
{
    static const char* const_kwlist[] = { "min_interval", "max_frames", NULL };
    static char** kwlist = const_cast<char**>(const_kwlist);
    double min_interval_f = 0.010; // Default 10ms period (100hz)
    double max_frames_f = 128;

    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|dd", kwlist, &min_interval_f, &max_frames_f)) {
        return NULL; // If an error occurs during argument parsing
    }

    // Set options
    _set_v2_interval(min_interval_f); // fractional seconds to us
    _set_cpu(true);                   // enable CPU profiling in echion
    init_frame_cache(1024);           // TODO don't hardcode this?

    _set_pid(getpid()); // TODO follow forks
    Renderer::get().set_renderer(_renderer);

    Py_BEGIN_ALLOW_THREADS;
    stack_sampler_v2();
    Py_END_ALLOW_THREADS;

    // DEBUGGING:  ensure uncaught exceptions dump core
    std::set_terminate(make_it_abort);
    return PyLong_FromLong(1);
}

static PyObject*
stop_stack_v2(PyObject* self, PyObject* args)
{
    // Input checks
    if (!PyArg_ParseTuple(args, "")) {
        return NULL; // If an error occurs during argument parsing
    }

    // Makes the sampler stop
    _stop_stack_v2.store(true);

    return PyLong_FromLong(1);
}

// This is a function for using std::cerr to print a number passed from Python,
// except it prepends the given string
static PyObject*
print_number(PyObject* self, PyObject* args)
{
    int num;
    char* str;
    if (!PyArg_ParseTuple(args, "si", &str, &num)) {
        return NULL; // If an error occurs during argument parsing
    }
    std::cerr << str << ": " << num << std::endl;
    return PyLong_FromLong(1);
}

static PyObject*
set_v2_interval(PyObject* self, PyObject* args)
{
    double new_interval;
    if (!PyArg_ParseTuple(args, "d", &new_interval)) {
        return NULL; // If an error occurs during argument parsing
    }
    _set_v2_interval(new_interval);
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef _stack_v2_methods[] = {
    { "start", (PyCFunction)start_stack_v2, METH_VARARGS | METH_KEYWORDS, "Start the sampler" },
    { "stop", (PyCFunction)stop_stack_v2, METH_VARARGS, "Stop the sampler" },
    { "set_interval", (PyCFunction)set_v2_interval, METH_VARARGS, "Set the sampling interval" },
    { "print_number", (PyCFunction)print_number, METH_VARARGS, "Print a number" },
    { NULL, NULL, 0, NULL }
};

PyMODINIT_FUNC
PyInit_stack_v2(void)
{
    PyObject* m;
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT, "_stack_v2", NULL, -1, _stack_v2_methods, NULL, NULL, NULL, NULL
    };

    m = PyModule_Create(&moduledef);
    if (!m)
        return NULL;

    return m;
}
