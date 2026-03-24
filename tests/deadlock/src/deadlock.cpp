/*
 * Deadlock Watchdog C extension for dd-trace-py tests.
 *
 * Provides arm() / disarm() to Python. When armed, a dedicated C++ thread
 * monitors test execution. On timeout it dumps every Python thread's stack to
 * stderr using GIL-free direct memory access (no GIL needed), then optionally
 * runs gdb / lldb for native backtraces, and finally aborts the process so CI
 * marks the run as failed rather than hanging forever.
 *
 * Design constraints
 * ------------------
 * - The GIL may be permanently held when we fire (that is the deadlock we are
 *   diagnosing). We MUST NOT call any Python API that acquires the GIL or
 *   allocates memory via Python's allocator in the dump path.
 * - We use write(2) on STDERR_FILENO directly – it is async-signal-safe and
 *   does not acquire any Python lock.
 * - Frame / thread / interpreter lists are traversed via raw pointer reads.
 *   In a deadlock all Python threads are blocked, so the data is stable. If
 *   a thread is not blocked we might read slightly stale data – acceptable for
 *   a debugging dump.
 *
 * Python version compatibility
 * ----------------------------
 * Python 3.8 – 3.10 : tstate->frame            (PyFrameObject*)
 * Python 3.11        : tstate->cframe->current_frame (_PyInterpreterFrame*)
 * Python 3.12+       : tstate->current_frame   (_PyInterpreterFrame*)
 *
 * _PyInterpreterFrame fields:
 * Python 3.11  : .f_code       (PyCodeObject*)
 * Python 3.12+ : .f_executable (PyObject*; may be a code or function object)
 *
 * Frame chain:
 * Python 3.11+ : frame->previous  (_PyInterpreterFrame*)
 * Python 3.10- : frame->f_back    (PyFrameObject*)
 *
 * Line numbers:
 * Python 3.10- : frame->f_lineno  (exact)
 * Python 3.11+ : code->co_firstlineno (approximate – function's first line).
 *   Exact line number requires interpreting the linetable with the instruction
 *   pointer from frame->prev_instr; that is complex to do safely without the
 *   GIL, so we accept the approximation here.
 */

/* Suppress HAVE_STD_ATOMIC conflicts between GCC and CPython internal headers */
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif

#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

/* Python 3.11+: _PyInterpreterFrame lives in an internal header */
#if PY_VERSION_HEX >= 0x030b0000
#include <internal/pycore_frame.h>
#endif

/* Python 3.14+: PyThreadState internals moved to pycore_tstate.h */
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_tstate.h>
#endif

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <mutex>
#include <thread>

#include <sys/wait.h> /* waitpid */
#include <unistd.h>   /* write, getpid, fork, dup2, sleep */

#ifdef __linux__
#include <fcntl.h>     /* open, O_RDONLY */
#include <sys/prctl.h> /* prctl(PR_SET_NAME, ...) */
#endif

#if defined(__MACH__)
#include <pthread.h> /* pthread_getname_np */
#endif

/* =========================================================================
 * Async-signal-safe / GIL-free write utilities
 *
 * AIDEV-NOTE: All write_* helpers call write(2) directly and perform no
 * heap allocation, no C++ exceptions, and no Python API calls. They are safe
 * to call when the GIL is held by another thread.
 * ========================================================================= */

static void
wfd(int fd, const char* s, ssize_t len = -1)
{
    if (!s)
        return;
    if (len < 0)
        len = (ssize_t)strlen(s);
    while (len > 0) {
        ssize_t n = write(fd, s, (size_t)len);
        if (n <= 0)
            break;
        s += n;
        len -= n;
    }
}

static void
wfd_char(int fd, char c)
{
    write(fd, &c, 1);
}

static void
wfd_ulong(int fd, unsigned long v, int base = 10)
{
    static const char digits[] = "0123456789abcdef";
    if (v == 0) {
        wfd_char(fd, '0');
        return;
    }
    char buf[64];
    int i = 0;
    while (v > 0 && i < 63) {
        buf[i++] = digits[v % (unsigned)base];
        v /= (unsigned)base;
    }
    for (int j = i - 1; j >= 0; j--)
        wfd_char(fd, buf[j]);
}

static void
wfd_long(int fd, long v)
{
    if (v < 0) {
        wfd_char(fd, '-');
        wfd_ulong(fd, (unsigned long)(-(v + 1)) + 1);
    } else {
        wfd_ulong(fd, (unsigned long)v);
    }
}

/* Write a PyUnicodeObject's content to fd WITHOUT holding the GIL.
 *
 * AIDEV-NOTE: Only compact-ASCII strings are rendered as text. This covers
 * virtually all Python source filenames and function/module names. Non-ASCII
 * or non-compact strings get a placeholder – that is acceptable for CI
 * debugging output. We do direct pointer arithmetic because we cannot call
 * PyUnicode_AsUTF8() without the GIL.
 */
static void
wfd_pyunicode(int fd, PyObject* obj)
{
    if (!obj) {
        wfd(fd, "<null>");
        return;
    }
    if (!PyUnicode_Check(obj)) {
        wfd(fd, "<not-unicode>");
        return;
    }
    if (PyUnicode_IS_COMPACT_ASCII(obj)) {
        const PyASCIIObject* aobj = (const PyASCIIObject*)obj;
        const char* data = (const char*)(aobj + 1); /* data follows header */
        Py_ssize_t length = aobj->length;
        /* Sanity-check length – guard against corrupt data during a deadlock */
        if (length > 0 && length < 65536) {
            wfd(fd, data, length);
            return;
        }
    }
    wfd(fd, "<non-ascii-or-invalid>");
}

/* Read the OS-level name for a thread into buf (NUL-terminated, max len-1
 * chars).  Returns true if a non-empty name was obtained.
 *
 * AIDEV-NOTE: On Linux we read /proc/self/task/<tid>/comm (16 chars max,
 * newline-terminated) using only open/read/close – all async-signal-safe.
 * On macOS we call pthread_getname_np with tstate->thread_id (which is the
 * pthread_t). Neither call acquires the GIL or Python allocator.
 */
static bool
get_thread_name(PyThreadState* tstate, char* buf, size_t len)
{
    if (!buf || len == 0)
        return false;
    buf[0] = '\0';

#if defined(__linux__)
    /* Build path: /proc/self/task/<native_thread_id>/comm */
    char path[64];
    unsigned long tid = (unsigned long)tstate->native_thread_id;
    /* Manual itoa into path to avoid snprintf (not async-signal-safe) */
    const char prefix[] = "/proc/self/task/";
    size_t pi = 0;
    while (pi < sizeof(path) - 1 && prefix[pi])
        path[pi] = prefix[pi++];
    /* Write tid digits */
    if (tid == 0) {
        if (pi < sizeof(path) - 1)
            path[pi++] = '0';
    } else {
        char tmp[24];
        int ti = 0;
        unsigned long v = tid;
        while (v > 0 && ti < (int)sizeof(tmp) - 1) {
            tmp[ti++] = (char)('0' + v % 10);
            v /= 10;
        }
        for (int j = ti - 1; j >= 0 && pi < sizeof(path) - 1; j--)
            path[pi++] = tmp[j];
    }
    const char suffix[] = "/comm";
    for (size_t si = 0; si < sizeof(suffix) && pi < sizeof(path) - 1; si++)
        path[pi++] = suffix[si]; /* includes NUL */

    int fd = open(path, O_RDONLY);
    if (fd >= 0) {
        ssize_t n = read(fd, buf, len - 1);
        close(fd);
        if (n > 0) {
            /* Strip trailing newline */
            if (buf[n - 1] == '\n')
                n--;
            buf[n] = '\0';
            return n > 0;
        }
    }
#elif defined(__MACH__)
    if (pthread_getname_np((pthread_t)tstate->thread_id, buf, len) == 0 && buf[0] != '\0')
        return true;
#endif
    return false;
}

/* =========================================================================
 * GIL-free Python frame / thread / interpreter walker
 * ========================================================================= */

static void
dump_one_frame(int fd, PyCodeObject* code, int lineno)
{
    if (!code) {
        wfd(fd, "    <unknown frame>\n");
        return;
    }
    wfd(fd, "    File \"");
    wfd_pyunicode(fd, code->co_filename);
    wfd(fd, "\", line ");
    wfd_long(fd, lineno);
    wfd(fd, ", in ");
#if PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11+: co_qualname gives the fully-qualified name */
    wfd_pyunicode(fd, code->co_qualname ? code->co_qualname : code->co_name);
#else
    wfd_pyunicode(fd, code->co_name);
#endif
    wfd(fd, "\n");
}

static void
dump_thread_frames(int fd, PyThreadState* tstate, long thread_num)
{
    wfd(fd, "\nThread ");
    wfd_long(fd, thread_num);
    wfd(fd, " (thread_id=0x");
    wfd_ulong(fd, (unsigned long)tstate->thread_id, 16);
    wfd(fd, ", native_id=");
    wfd_ulong(fd, (unsigned long)tstate->native_thread_id);
    {
        char name[64];
        if (get_thread_name(tstate, name, sizeof(name))) {
            wfd(fd, ", name=\"");
            wfd(fd, name);
            wfd(fd, "\"");
        }
    }
    wfd(fd, "):\n  Traceback (most recent frame first):\n");

#if PY_VERSION_HEX >= 0x030b0000
    /* AIDEV-NOTE: Python 3.11 uses tstate->cframe->current_frame;
     *             Python 3.12+ removed _PyCFrame and exposes current_frame
     *             directly in PyThreadState. Both are in cpython/pystate.h
     *             which Python.h includes when Py_BUILD_CORE is defined.
     *
     *             Line numbers use co_firstlineno (function's first line) as
     *             an approximation. Precise line numbers would require
     *             interpreting the linetable with frame->prev_instr – complex
     *             and risky to do without the GIL.
     */
    _PyInterpreterFrame* frame;
#if PY_VERSION_HEX >= 0x030c0000
    frame = tstate->current_frame; /* Python 3.12+ */
#else
    frame = tstate->cframe ? tstate->cframe->current_frame : nullptr; /* 3.11 */
#endif

    int depth = 0;
    while (frame != nullptr && depth < 128) {
        PyCodeObject* code = nullptr;

#if PY_VERSION_HEX >= 0x030c0000
        /* Python 3.12+: f_executable may be a PyCodeObject or a function */
        PyObject* exec = frame->f_executable;
        if (exec) {
            if (PyCode_Check(exec)) {
                code = (PyCodeObject*)exec;
            }
            /* AIDEV-NOTE: If f_executable is a function object (e.g. for
             * generator/coroutine entry frames), we skip it here rather than
             * dereferencing PyFunctionObject::func_code, which could race
             * against GC in a partially-deadlocked process. The parent frame
             * will carry the real code object.
             */
        }
#else
        /* Python 3.11: f_code is always PyCodeObject* */
        code = frame->f_code;
#endif
        if (code && PyCode_Check((PyObject*)code)) {
            dump_one_frame(fd, code, (int)code->co_firstlineno);
        }

        frame = frame->previous;
        depth++;
    }
    if (depth == 0)
        wfd(fd, "    <no frames>\n");

#else /* Python 3.10 and below */

    PyFrameObject* frame = tstate->frame;
    int depth = 0;
    while (frame != nullptr && depth < 128) {
        PyCodeObject* code = frame->f_code;
        if (code)
            dump_one_frame(fd, code, (int)frame->f_lineno);
        frame = frame->f_back;
        depth++;
    }
    if (depth == 0)
        wfd(fd, "    <no frames>\n");
#endif
}

/* Walk every interpreter and every thread – NO GIL held.
 *
 * AIDEV-NOTE: We use the stable public C API:
 *   PyInterpreterState_Head()       – returns _PyRuntime.interpreters.head
 *   PyInterpreterState_Next()       – follows interp->next
 *   PyInterpreterState_ThreadHead() – returns interp->tstate_head (or
 *                                     interp->threads.head on 3.12+)
 *   PyThreadState_Next()            – follows tstate->next
 * These functions are simple pointer reads and do not acquire the GIL.
 */
static void
dump_all_python_threads(int fd)
{
    wfd(fd,
        "\n"
        "================================================================================\n"
        "DEADLOCK WATCHDOG: Test timed out -- dumping Python thread stacks (GIL-free)\n"
        "================================================================================\n");

    PyInterpreterState* interp = PyInterpreterState_Head();
    if (!interp) {
        wfd(fd, "No interpreters found (Python not initialised?)\n");
        wfd(fd, "================================================================================\n\n");
        return;
    }

    long interp_num = 0;
    while (interp) {
        wfd(fd, "\nInterpreter ");
        wfd_long(fd, interp_num);
        wfd(fd, ":\n");

        PyThreadState* tstate = PyInterpreterState_ThreadHead(interp);
        long thread_num = 0;
        while (tstate) {
            dump_thread_frames(fd, tstate, thread_num);
            tstate = PyThreadState_Next(tstate);
            thread_num++;
        }
        if (thread_num == 0)
            wfd(fd, "  (no threads)\n");

        interp = PyInterpreterState_Next(interp);
        interp_num++;
    }

    wfd(fd, "\n================================================================================\n\n");
}

/* =========================================================================
 * Native backtrace via gdb / lldb
 *
 * AIDEV-NOTE: We fork() and exec the debugger in the child so the parent
 * process (the deadlocked test runner) is not affected. fork() is safe to
 * call from any thread. exec() replaces the child image entirely.
 * snprintf() is not async-signal-safe but is safe in the child since it
 * is single-threaded after fork().
 * ========================================================================= */

static void
run_debugger_backtraces(int fd)
{
    wfd(fd,
        "\n================================================================================\n"
        "DEADLOCK WATCHDOG: Native thread backtraces\n"
        "================================================================================\n");

    pid_t our_pid = getpid();
    pid_t child = fork();

    if (child < 0) {
        wfd(fd, "fork() failed -- cannot run debugger\n");
        return;
    }

    if (child == 0) {
        /* Child: redirect stdout -> stderr so output lands in CI logs */
        dup2(STDERR_FILENO, STDOUT_FILENO);

        char pid_str[32];
        snprintf(pid_str, sizeof(pid_str), "%d", (int)our_pid);

#ifdef __linux__
        const char* gdb_argv[] = { "gdb",     "--batch",
                                   "--quiet", "--nx",
                                   "-p",      pid_str,
                                   "-ex",     "set print thread-events off",
                                   "-ex",     "thread apply all bt",
                                   nullptr };
        execvp("gdb", (char* const*)gdb_argv);
        /* execvp only returns on failure */
        write(STDERR_FILENO, "gdb not found\n", 14);
#elif defined(__MACH__)
        const char* lldb_argv[] = { "lldb", "--batch", "-p",   pid_str, "-o", "thread backtrace all",
                                    "-o",   "quit",    nullptr };
        execvp("lldb", (char* const*)lldb_argv);
        write(STDERR_FILENO, "lldb not found\n", 15);
#else
        write(STDERR_FILENO, "no debugger available on this platform\n", 38);
#endif
        _exit(1);
    }

    /* Parent: wait for debugger to finish (60 s max) */
    for (int i = 0; i < 60; i++) {
        int status = 0;
        pid_t ret = waitpid(child, &status, WNOHANG);
        if (ret == child || ret < 0)
            break;
        sleep(1);
    }

    wfd(fd, "\n================================================================================\n\n");
}

/* =========================================================================
 * Watchdog background thread
 * ========================================================================= */

struct WatchdogState
{
    std::atomic<bool> armed{ false };
    std::mutex mtx;
    std::condition_variable cv;
    int timeout_seconds = 300;
    bool enable_gdb = false;
    /* Optional test node-id for the dump header (e.g. "tests/foo::test_bar") */
    char test_name[512] = {};
    /* Joinable watchdog thread – never detached so we can join on disarm/exit.
     * AIDEV-NOTE: Storing the thread here (rather than detaching it) prevents
     * undefined behaviour in ~condition_variable() which would otherwise be
     * destroyed while the thread is still blocked on cv.wait_until(). */
    std::thread watchdog_thread;

    ~WatchdogState()
    {
        /* Wake the thread (if running) so it exits cleanly before the cv and
         * mutex members are destroyed. */
        armed.store(false);
        cv.notify_all();
        if (watchdog_thread.joinable())
            watchdog_thread.join();
    }
};

static WatchdogState g_wd; /* NOLINT(cppcoreguidelines-avoid-non-const-global-variables) */

static void
set_thread_name(const char* name)
{
#ifdef __linux__
    prctl(PR_SET_NAME, name, 0, 0, 0);
#elif defined(__MACH__)
    pthread_setname_np(name);
#else
    (void)name;
#endif
}

static void
watchdog_thread_fn(int timeout_secs, bool enable_gdb)
{
    set_thread_name("dd-test-wdg");

    std::unique_lock<std::mutex> lock(g_wd.mtx);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_secs);

    /* Block until disarmed or timeout.
     *
     * AIDEV-NOTE: We use the predicate form of wait_until so that a disarm()
     * call that fires *before* this thread enters the wait (a real race when
     * arm()/disarm() are called in rapid succession) is not lost: the predicate
     * is evaluated immediately on entry and the wait is skipped if already
     * satisfied.  Without the predicate, notify_all() fired before wait_until()
     * is reached would be silently dropped, causing join() in disarm() to block
     * until the full timeout expires.
     */
    bool disarmed = g_wd.cv.wait_until(lock, deadline, [] { return !g_wd.armed.load(); });

    if (!disarmed) {
        /* Timeout fired while still armed -- test is stuck */
        wfd(STDERR_FILENO, "\n!!! DEADLOCK WATCHDOG FIRED: timeout=");
        wfd_long(STDERR_FILENO, timeout_secs);
        wfd(STDERR_FILENO, "s");
        if (g_wd.test_name[0] != '\0') {
            wfd(STDERR_FILENO, " test=");
            wfd(STDERR_FILENO, g_wd.test_name);
        }
        wfd(STDERR_FILENO, " !!!\n");

        lock.unlock();

        dump_all_python_threads(STDERR_FILENO);

        if (enable_gdb)
            run_debugger_backtraces(STDERR_FILENO);

        /* Abort so CI marks the build as failed rather than timing out later.
         * SIGABRT also generates a core dump when ulimit -c is configured,
         * which pairs nicely with the Python-level stack dump above.
         *
         * AIDEV-NOTE: We send SIGABRT to the whole process (not just this
         * thread) via kill() so Python's main thread cannot silently swallow
         * it via a signal handler that just sets a flag.
         */
        kill(getpid(), SIGABRT);
        return;
    }

    /* Notified before timeout (disarmed) -- nothing to do */
}

/* =========================================================================
 * Python module API
 * ========================================================================= */

static PyObject*
py_arm(PyObject* /* self */, PyObject* args, PyObject* kwargs)
{
    static const char* kwlist[] = { "timeout", "enable_gdb", "test_name", nullptr };

    int timeout = 300;
    int enable_gdb = 0;
    const char* test_name = nullptr;

    if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "|ipz", const_cast<char**>(kwlist), &timeout, &enable_gdb, &test_name))
        return nullptr;

    if (timeout <= 0) {
        PyErr_SetString(PyExc_ValueError, "timeout must be a positive integer");
        return nullptr;
    }

    /* Disarm any currently running watchdog and join its thread first. */
    {
        std::lock_guard<std::mutex> lock(g_wd.mtx);
        g_wd.armed.store(false);
    }
    g_wd.cv.notify_all();
    /* Join outside the mutex: the thread reacquires the mutex after waking,
     * so we must not hold it here or we deadlock. */
    if (g_wd.watchdog_thread.joinable())
        g_wd.watchdog_thread.join();

    /* Configure and arm the new watchdog */
    {
        std::lock_guard<std::mutex> lock(g_wd.mtx);
        g_wd.timeout_seconds = timeout;
        g_wd.enable_gdb = (enable_gdb != 0);
        if (test_name) {
            strncpy(g_wd.test_name, test_name, sizeof(g_wd.test_name) - 1);
            g_wd.test_name[sizeof(g_wd.test_name) - 1] = '\0';
        } else {
            g_wd.test_name[0] = '\0';
        }
        g_wd.armed.store(true);
    }

    /* Launch the watchdog thread (joinable, stored in g_wd.watchdog_thread). */
    g_wd.watchdog_thread = std::thread(watchdog_thread_fn, timeout, enable_gdb != 0);

    Py_RETURN_NONE;
}

static PyObject*
py_disarm(PyObject* /* self */, PyObject* /* args */)
{
    {
        std::lock_guard<std::mutex> lock(g_wd.mtx);
        g_wd.armed.store(false);
    }
    g_wd.cv.notify_all();
    /* Join outside the mutex so the thread can reacquire it to exit. */
    if (g_wd.watchdog_thread.joinable())
        g_wd.watchdog_thread.join();
    Py_RETURN_NONE;
}

static PyMethodDef deadlock_methods[] = {
    { "arm",
      (PyCFunction)py_arm,
      METH_VARARGS | METH_KEYWORDS,
      "arm(timeout=300, enable_gdb=False, test_name=None)\n\n"
      "Start the deadlock watchdog. If the current test does not complete\n"
      "within `timeout` seconds, dumps all Python thread stacks to stderr\n"
      "without acquiring the GIL (safe even when the GIL is held in a\n"
      "deadlock). If `enable_gdb` is True, also runs gdb/lldb for native\n"
      "backtraces. The process is then aborted via SIGABRT.\n"
      "The optional `test_name` is included in the dump header." },
    { "disarm", py_disarm, METH_NOARGS, "disarm()\n\nCancel the current deadlock watchdog." },
    { nullptr, nullptr, 0, nullptr }
};

static struct PyModuleDef deadlock_module = { PyModuleDef_HEAD_INIT,
                                              "_deadlock",
                                              "Deadlock detector: GIL-free Python thread stack dumper for CI.\n\n"
                                              "Provides arm() / disarm() used by the deadlock_watchdog pytest fixture\n"
                                              "in tests/deadlock/conftest.py.",
                                              -1,
                                              deadlock_methods };

PyMODINIT_FUNC
PyInit__deadlock(void)
{
    return PyModule_Create(&deadlock_module);
}
