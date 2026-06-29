#pragma once

// Self-contained native upload thread for the profiler.
//
// This lives in the profiling extension on purpose: it lets the profiler drive
// periodic uploads from its own OS thread without depending on dd-trace-py's
// PeriodicThread/PeriodicService machinery.
//
// Forward-declare PyObject to avoid including Python.h in this header (CPython
// headers use old-style casts which our build treats as errors). Full Python
// access lives in upload_thread.cpp.
// NOLINTBEGIN(bugprone-reserved-identifier) -- must match CPython's struct name
struct _object;
typedef struct _object PyObject;
// NOLINTEND(bugprone-reserved-identifier)

#ifdef __cplusplus
extern "C"
{
#endif

    // Start the profiler's native upload thread.
    //
    // `tick` is a Python callable taking no arguments and returning the next
    // interval in seconds (float or int); a negative/invalid return keeps the
    // current interval. The thread takes ownership of a reference to `tick`.
    //
    // Must be called with the GIL held. No-op if the thread is already running.
    void ddup_upload_thread_start(double interval_s, PyObject* tick);

    // Signal the upload thread to stop and join it.
    //
    // Must be called with the GIL released (the worker may need the GIL to
    // finish an in-flight tick before it can observe the stop request). No-op
    // if the thread is not running.
    void ddup_upload_thread_stop();

#ifdef __cplusplus
} // extern "C"
#endif
