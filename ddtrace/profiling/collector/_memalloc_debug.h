#ifndef _DDTRACE_MEMALLOC_DEBUG_H
#define _DDTRACE_MEMALLOC_DEBUG_H

#include <assert.h>
#include <stdbool.h>

#include <Python.h>

/* Release the GIL. For debugging when GIL release allows memory profiling functions
 * to interleave from different threads. Call near C Python API calls. */
static inline void
memalloc_debug_gil_release(void)
{
#ifndef NDEBUG
    Py_BEGIN_ALLOW_THREADS;
    Py_END_ALLOW_THREADS;
#endif
}

typedef struct
{
    bool acquired;
} memalloc_gil_debug_check_t;

static void
memalloc_gil_debug_check_init(memalloc_gil_debug_check_t* c)
{
    c->acquired = false;
}

/* Annotate that we are beginning a critical section where we don't want other
 * memalloc code to run. If compiled assertions enabled, this will check that the
 * GIL is held and that the guard has not already been acquired elsewhere.
 *
 * This is a macro so we get file/line info where it's actually used */
#define MEMALLOC_GIL_DEBUG_CHECK_ACQUIRE(c)                                                                            \
    {                                                                                                                  \
        memalloc_gil_debug_check_t* p = c;                                                                             \
        assert(PyGILState_Check());                                                                                    \
        assert(!p->acquired);                                                                                          \
        p->acquired = true;                                                                                            \
    }

/* Annotate that we are ending a critical section where we don't want other
 * memalloc code to run. If compiled assertions enabled, this will check that the
 * guard is acquired.
 *
 * This is a macro so we get file/line info where it's actually used */
#define MEMALLOC_GIL_DEBUG_CHECK_RELEASE(c)                                                                            \
    {                                                                                                                  \
        memalloc_gil_debug_check_t* p = c;                                                                             \
        assert(p->acquired);                                                                                           \
        p->acquired = false;                                                                                           \
    }

#endif
