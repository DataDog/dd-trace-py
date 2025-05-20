#ifndef _DDTRACE_MEMALLOC_DEBUG_H
#define _DDTRACE_MEMALLOC_DEBUG_H

/* Release the GIL. For debugging when GIL release allows memory profiling functions
 * to interleave from different threads. Call near C Python API calls. */
static inline void
memalloc_debug_gil_release(void)
{
#ifdef MEMALLOC_TESTING_GIL_RELEASE
    Py_BEGIN_ALLOW_THREADS;
    Py_END_ALLOW_THREADS;
#endif
}

#endif
