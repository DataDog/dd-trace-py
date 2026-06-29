#define _GNU_SOURCE
#define PY_SSIZE_T_CLEAN

#include <Python.h>

#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static uint64_t
thread_cpu_time_ns(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) != 0) {
        return 0;
    }
    return ((uint64_t)ts.tv_sec * 1000000000ULL) + (uint64_t)ts.tv_nsec;
}

static void
burn_thread_cpu_ms(long duration_ms)
{
    const uint64_t start = thread_cpu_time_ns();
    const uint64_t duration_ns = (uint64_t)duration_ms * 1000000ULL;
    const uint64_t deadline = start + duration_ns;
    volatile uint64_t value = 0x9e3779b97f4a7c15ULL;

    while (thread_cpu_time_ns() < deadline) {
        value ^= value << 7;
        value ^= value >> 9;
        value *= 0xbf58476d1ce4e5b9ULL;
    }
}

static int
raw_ppoll_with_pending_sigprof_impl(long burn_ms, long timeout_ms, int use_raw_syscall)
{
    sigset_t block_sigprof;
    sigset_t old_mask;
    sigset_t wait_mask;

    sigemptyset(&block_sigprof);
    sigaddset(&block_sigprof, SIGPROF);

    int mask_rc = pthread_sigmask(SIG_BLOCK, &block_sigprof, &old_mask);
    if (mask_rc != 0) {
        return mask_rc;
    }

    burn_thread_cpu_ms(burn_ms);

    wait_mask = old_mask;
    sigdelset(&wait_mask, SIGPROF);

    struct timespec timeout;
    timeout.tv_sec = timeout_ms / 1000;
    timeout.tv_nsec = (timeout_ms % 1000) * 1000000L;

    errno = 0;
    int rc;
    if (use_raw_syscall) {
#ifdef SYS_ppoll
        /*
         * The raw Linux ppoll syscall takes a kernel sigset size, not
         * sizeof(sigset_t). On the 64-bit Linux targets used for this profiler
         * test, kernel_sigset_t is one unsigned long.
         */
        rc = (int)syscall(SYS_ppoll, NULL, 0, &timeout, &wait_mask, sizeof(unsigned long));
#else
        rc = ppoll(NULL, 0, &timeout, &wait_mask);
#endif
    } else {
        rc = ppoll(NULL, 0, &timeout, &wait_mask);
    }
    const int saved_errno = errno;

    mask_rc = pthread_sigmask(SIG_SETMASK, &old_mask, NULL);
    if (mask_rc != 0) {
        return mask_rc;
    }

    if (rc == -1) {
        return saved_errno;
    }
    return 0;
}

static PyObject*
py_raw_ppoll_with_pending_sigprof(PyObject* self, PyObject* args, PyObject* kwargs)
{
    long burn_ms = 50;
    long timeout_ms = 500;
    int release_gil = 1;
    int use_raw_syscall = 1;
    static char* kwlist[] = { "burn_ms", "timeout_ms", "release_gil", "use_raw_syscall", NULL };

    if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "|llpp", kwlist, &burn_ms, &timeout_ms, &release_gil, &use_raw_syscall)) {
        return NULL;
    }

    int result;
    if (release_gil) {
        Py_BEGIN_ALLOW_THREADS result = raw_ppoll_with_pending_sigprof_impl(burn_ms, timeout_ms, use_raw_syscall);
        Py_END_ALLOW_THREADS
    } else {
        result = raw_ppoll_with_pending_sigprof_impl(burn_ms, timeout_ms, use_raw_syscall);
    }

    return PyLong_FromLong(result);
}

struct raw_pthread_args
{
    long burn_ms;
};

static void*
raw_pthread_burn_main(void* opaque)
{
    struct raw_pthread_args* args = (struct raw_pthread_args*)opaque;
    burn_thread_cpu_ms(args->burn_ms);
    return NULL;
}

static PyObject*
py_raw_pthread_burn_cpu(PyObject* self, PyObject* args)
{
    long burn_ms = 250;
    if (!PyArg_ParseTuple(args, "|l", &burn_ms)) {
        return NULL;
    }

    pthread_t thread;
    struct raw_pthread_args pthread_args;
    pthread_args.burn_ms = burn_ms;

    int rc = pthread_create(&thread, NULL, raw_pthread_burn_main, &pthread_args);
    if (rc != 0) {
        return PyLong_FromLong(rc);
    }

    Py_BEGIN_ALLOW_THREADS rc = pthread_join(thread, NULL);
    Py_END_ALLOW_THREADS

      return PyLong_FromLong(rc);
}

static PyMethodDef module_methods[] = {
    { "raw_ppoll_with_pending_sigprof",
      (PyCFunction)py_raw_ppoll_with_pending_sigprof,
      METH_VARARGS | METH_KEYWORDS,
      "Block SIGPROF, burn thread CPU, then atomically unblock SIGPROF inside ppoll without retrying EINTR." },
    { "raw_pthread_burn_cpu",
      py_raw_pthread_burn_cpu,
      METH_VARARGS,
      "Start and join a raw pthread that burns CPU without entering Python." },
    { NULL, NULL, 0, NULL },
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT, "native_cpu_timer_syscall_hazards", "Native helpers for CPU timer syscall hazard tests.", -1,
    module_methods,
};

PyMODINIT_FUNC
PyInit_native_cpu_timer_syscall_hazards(void)
{
    return PyModule_Create(&module_definition);
}
