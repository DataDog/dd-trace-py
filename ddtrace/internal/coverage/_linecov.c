/*
 * Per-test line coverage on sys.monitoring LINE events (CPython 3.12+).
 *
 * Where a line event goes, exactly one of:
 * - the file's initialiser lines, when the code object is initialiser code (a module body, or a
 *   class body nested statically in it);
 * - the context found in the ContextVar, when it is active: a test, or a suite-scope fixture setup;
 * - the file's unowned lines otherwise: no context, or a context whose window has closed.
 * A fixture context's lines also join the file's suite-setup lines when it is popped. The three
 * per-file sets are the process's Shared record. A test's record is its own lines, the lines of
 * the fixture contexts it depends on, and the initialiser lines of every file those touch.
 *
 * One-shot: the callback returns DISABLE once no active context lacks the line, so a line start
 * fires about once per context. Initialiser lines are disabled at once and never re-armed, because
 * their sets are process-wide. Every other code object with a disabled line is listed, and pushing
 * a context re-arms the listed ones, so each context sees every line it runs, wherever the line
 * ran first.
 *
 * Re-arming:
 * - internal: re-instrument only the line starts of the listed code objects, as CPython's
 *   add_line_tools() does. Used when a self-test at init passes;
 * - public: sys.monitoring.set_local_events(tool, code, 0) then (tool, code, LINE), which costs
 *   O(code object size).
 *
 * INSTRUCTION probes (CPython 3.12 only): each LINE event computes the line of the instruction and
 * of the one before it. CPython 3.12 stores a line as a one-byte delta from where the instruction's
 * offset predicts it, and scans the line table from the start for any instruction whose delta does
 * not fit. At its first LINE event, a code object with such an instruction moves to INSTRUCTION
 * events on its line starts only, the instructions where CPython puts LINE events: the callback
 * gets the instruction offset and reads the line from a table computed once. Every other
 * instruction gets back the opcode it had. Both kinds deliver the same lines per context, except
 * that a probe also fires where CPython suppresses a LINE event because the previous instruction
 * ran on the same line, as when a coroutine resumes at an await: the await line can then be
 * credited again, to the context the coroutine runs in.
 *
 * The callbacks never raise into the monitored code: an internal error is counted and the line
 * stays armed. All state is guarded by the GIL. Free-threaded builds are not supported.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#if PY_VERSION_HEX < 0x030C0000
#error "_linecov requires CPython 3.12 or later"
#endif

#ifdef Py_GIL_DISABLED
#define DD_LINECOV_FREE_THREADED 1
#else
#define DD_LINECOV_FREE_THREADED 0
#endif

/* Keeps rarely run code, such as the per-code-object setup, out of the event callbacks. */
#if defined(__GNUC__) || defined(__clang__)
#define DD_COLD __attribute__((cold, noinline))
#define DD_LIKELY(x) __builtin_expect(!!(x), 1)
#define DD_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define DD_COLD
#define DD_LIKELY(x) (x)
#define DD_UNLIKELY(x) (x)
#endif

/* ------------------------------------------------------------------------------------------------
 * CPython monitoring data layout, needed only by the internal re-arm.
 * 3.12 and 3.13 declare it in the public cpython/code.h; 3.14 and 3.15 moved it to an internal
 * header, so it is mirrored here. The init self-test disables the internal re-arm if it is wrong.
 */
#if PY_VERSION_HEX >= 0x030E0000 && PY_VERSION_HEX < 0x03100000
#define DD_HAVE_INTERNAL_REARM 1
typedef struct
{
    uint8_t tools[11];
} dd_local_monitors;
typedef struct
{
    uint8_t bytes_per_entry;
    uint8_t data[1];
} dd_line_data;
typedef struct
{
    dd_local_monitors local_monitors;
    dd_local_monitors active_monitors;
    uint8_t* tools;
    uintptr_t tool_versions[8];
    dd_line_data* lines;
    uint8_t* line_tools;
    uint8_t* per_instruction_opcodes;
    uint8_t* per_instruction_tools;
} dd_monitoring;
#define DD_ORIGINAL_OPCODE_PTR(m, i) (&(m)->lines->data[(i) * (m)->lines->bytes_per_entry])
#elif PY_VERSION_HEX >= 0x030D0000 && PY_VERSION_HEX < 0x030E0000
#define DD_HAVE_INTERNAL_REARM 1
typedef _PyCoMonitoringData dd_monitoring;
#define DD_ORIGINAL_OPCODE_PTR(m, i) (&(m)->lines->data[(i) * (m)->lines->bytes_per_entry])
#elif PY_VERSION_HEX >= 0x030C0000 && PY_VERSION_HEX < 0x030D0000
#define DD_HAVE_INTERNAL_REARM 1
typedef _PyCoMonitoringData dd_monitoring;
#define DD_ORIGINAL_OPCODE_PTR(m, i) (&(m)->lines[(i)].original_opcode)
#else
#define DD_HAVE_INTERNAL_REARM 0
#endif

#if DD_LINECOV_FREE_THREADED
#undef DD_HAVE_INTERNAL_REARM
#define DD_HAVE_INTERNAL_REARM 0
#endif

/* CPython 3.13 and later store line deltas wide enough that LINE events never scan the line table. */
#if DD_HAVE_INTERNAL_REARM && PY_VERSION_HEX < 0x030D0000
#define DD_HAVE_INSTRUCTION_PROBES 1
#else
#define DD_HAVE_INSTRUCTION_PROBES 0
#endif

/* ------------------------------------------------------------------------------------------------
 * Bitsets: one bit per line number, byte = line / 8, bit = 0x80 >> (line % 8), which is the
 * payload format the intake expects.
 */
typedef struct
{
    uint8_t* bits;
    uint32_t nbytes; /* allocated */
    uint32_t used;   /* bytes up to the last one with a bit set */
} Bitset;

static DD_COLD int
bitset_reserve(Bitset* b, uint32_t nbytes)
{
    if (nbytes <= b->nbytes)
        return 0;
    uint32_t size = b->nbytes ? b->nbytes : 32;
    while (size < nbytes)
        size *= 2;
    uint8_t* bits = PyMem_RawRealloc(b->bits, size);
    if (bits == NULL)
        return -1;
    memset(bits + b->nbytes, 0, size - b->nbytes);
    b->bits = bits;
    b->nbytes = size;
    return 0;
}

static inline int
bitset_set(Bitset* b, uint32_t line)
{
    uint32_t byte = line >> 3;
    if (DD_UNLIKELY(byte >= b->nbytes) && bitset_reserve(b, byte + 1) < 0)
        return -1;
    b->bits[byte] |= (uint8_t)(0x80 >> (line & 7));
    if (byte >= b->used)
        b->used = byte + 1;
    return 0;
}

static inline int
bitset_has(const Bitset* b, uint32_t line)
{
    uint32_t byte = line >> 3;
    return byte < b->used && (b->bits[byte] & (uint8_t)(0x80 >> (line & 7)));
}

static int
bitset_or(Bitset* dst, const Bitset* src)
{
    if (src->used == 0)
        return 0;
    if (bitset_reserve(dst, src->used) < 0)
        return -1;
    for (uint32_t i = 0; i < src->used; i++)
        dst->bits[i] |= src->bits[i];
    if (src->used > dst->used)
        dst->used = src->used;
    return 0;
}

static inline uint32_t
bitset_used(const Bitset* b)
{
    return b->used;
}

static void
bitset_free(Bitset* b)
{
    PyMem_RawFree(b->bits);
    b->bits = NULL;
    b->nbytes = 0;
    b->used = 0;
}

/* ------------------------------------------------------------------------------------------------
 * Files and code objects.
 */
typedef struct
{
    PyObject* path;        /* absolute path, as instrumented */
    PyObject* report_path; /* path in the payload, or None when outside the workspace */
    Bitset init_lines;     /* lines run in this file's initialiser code */
    Bitset suite_lines;    /* lines run by suite-scope fixture setups */
    Bitset unowned_lines;  /* lines run outside every active context */
    uint32_t hint_nbytes;  /* largest context bitmap seen, to size new ones at once */
} FileInfo;

static FileInfo* g_files = NULL;
static int32_t g_n_files = 0;
static int32_t g_cap_files = 0;

enum
{
    PROBES_UNDECIDED = 0, /* until the code object's first LINE event */
    PROBES_LINE = 1,
    PROBES_INSTRUCTION = 2, /* INSTRUCTION events on the line starts */
};

typedef struct
{
    PyObject* code; /* borrowed: the code object owns this struct through co_extra */
    int32_t file_id;
    uint8_t is_init;  /* initialiser code: its lines go to the file's initialiser lines */
    uint8_t dirty;    /* listed in g_dirty since the last re-arm */
    uint8_t rearm;    /* 0: not decided, 1: internal, 2: public toggle */
    uint8_t probes;   /* PROBES_* */
    int32_t n_starts; /* line-start code unit indices, computed on the first internal re-arm */
    int32_t* starts;
    int32_t* start_lines; /* the line of each start, for INSTRUCTION probes */
} CodeInfo;

static Py_ssize_t g_extra_index = -1;

static void
codeinfo_free(void* p)
{
    CodeInfo* ci = (CodeInfo*)p;
    if (ci == NULL)
        return;
    PyMem_RawFree(ci->starts);
    PyMem_RawFree(ci->start_lines);
    PyMem_RawFree(ci);
}

/* CPython's _PyCodeObjectExtra, the layout of co_extra since 3.6. Read directly, because the events
   look the CodeInfo up for every line: PyUnstable_Code_GetExtra() does the same, behind a call. */
typedef struct
{
    Py_ssize_t ce_size;
    void* ce_extras[1];
} dd_code_extra;

static inline CodeInfo*
codeinfo_get(PyObject* code)
{
    if (DD_UNLIKELY(!PyCode_Check(code)))
        return NULL;
    dd_code_extra* extra = (dd_code_extra*)((PyCodeObject*)code)->co_extra;
    if (extra == NULL || g_extra_index < 0 || g_extra_index >= extra->ce_size)
        return NULL;
    return (CodeInfo*)extra->ce_extras[g_extra_index];
}

/* Code objects with a line disabled since the last re-arm, by CodeInfo. The list holds a strong
   reference to each code object, so a listed CodeInfo cannot be freed before it is re-armed. */
static CodeInfo** g_dirty = NULL;
static Py_ssize_t g_n_dirty = 0;
static Py_ssize_t g_cap_dirty = 0;

/* ------------------------------------------------------------------------------------------------
 * Contexts.
 */
enum
{
    CONTEXT_TEST = 0,
    CONTEXT_FIXTURE = 1,
};

typedef struct Context
{
    PyObject_HEAD Bitset* files; /* indexed by file id */
    int32_t cap_files;
    int32_t* touched; /* file ids with at least one line, in first-touch order */
    int32_t n_touched;
    int32_t cap_touched;
    PyObject* fixtures; /* list of the fixture contexts this one depends on, or NULL */
    int kind;
    int active;
    int index; /* position in g_active while active */
} Context;

static PyTypeObject ContextType;

static Context** g_active = NULL;
static int g_n_active = 0;
static int g_cap_active = 0;

/* Configuration, set by init(). */
static int g_initialized = 0;
static int g_tool_id = -1;
static uint8_t g_tool_bit = 0;
static int g_line_event_index = -1;
static long g_line_event_mask = 0;
static PyObject* g_disable = NULL;
static PyObject* g_set_local_events = NULL;
static PyObject* g_tool_id_object = NULL;   /* tool id, as set_local_events takes it */
static PyObject* g_line_mask_object = NULL; /* LINE event set, as set_local_events takes it */
static PyObject* g_generic_scope_prefix = NULL;
static PyObject* g_ctxvar = NULL;
static int g_internal_rearm = 0;
static uint8_t g_deopt[256];
static int g_instrumented_line = -1;
static int g_enter_executor = -1;
static int g_instrumented_instruction = -1;
static int g_instruction_event_index = -1;
static PyObject* g_instruction_mask_object = NULL; /* INSTRUCTION event set, as set_local_events takes it */
static int g_instruction_probes = 0;
static int g_min_instrumented = 256;    /* opcodes from this one up are instrumented opcodes */
static uint8_t g_superinstruction[256]; /* 1 for an opcode that also runs the next instruction */

/* Counters, for profiling and tests. */
static unsigned long long g_events = 0;
static unsigned long long g_events_init = 0;
static unsigned long long g_events_unowned = 0;
static unsigned long long g_events_kept = 0;
static unsigned long long g_events_instruction = 0;
static unsigned long long g_errors = 0;
static unsigned long long g_rearm_internal = 0;
static unsigned long long g_rearm_public = 0;
static unsigned long long g_rearm_instruction = 0;
static unsigned long long g_rearm_starts = 0;
static unsigned long long g_switched = 0;
static unsigned long long g_reverted = 0;

static DD_COLD int
context_reserve_files(Context* c, int32_t file_id)
{
    if (file_id < c->cap_files)
        return 0;
    int32_t cap = c->cap_files ? c->cap_files : 64;
    while (cap <= file_id)
        cap *= 2;
    Bitset* files = PyMem_RawRealloc(c->files, sizeof(Bitset) * (size_t)cap);
    if (files == NULL)
        return -1;
    memset(files + c->cap_files, 0, sizeof(Bitset) * (size_t)(cap - c->cap_files));
    c->files = files;
    c->cap_files = cap;
    return 0;
}

static int
context_touch(Context* c, int32_t file_id)
{
    if (c->n_touched == c->cap_touched) {
        int32_t cap = c->cap_touched ? c->cap_touched * 2 : 16;
        int32_t* touched = PyMem_RawRealloc(c->touched, sizeof(int32_t) * (size_t)cap);
        if (touched == NULL)
            return -1;
        c->touched = touched;
        c->cap_touched = cap;
    }
    c->touched[c->n_touched++] = file_id;
    return 0;
}

/* The first line of a file in the context, or a line past the end of its bitmap. */
static DD_COLD int
context_set_slow(Context* c, int32_t file_id, uint32_t line)
{
    if (file_id >= c->cap_files && context_reserve_files(c, file_id) < 0)
        return -1;
    Bitset* b = &c->files[file_id];
    if (b->nbytes == 0) {
        uint32_t hint = g_files[file_id].hint_nbytes;
        if (bitset_reserve(b, hint > (line >> 3) ? hint : (line >> 3) + 1) < 0)
            return -1;
        if (context_touch(c, file_id) < 0) {
            bitset_free(b);
            return -1;
        }
    }
    if (bitset_set(b, line) < 0)
        return -1;
    if (b->nbytes > g_files[file_id].hint_nbytes)
        g_files[file_id].hint_nbytes = b->nbytes;
    return 0;
}

static inline int
context_set(Context* c, int32_t file_id, uint32_t line)
{
    uint32_t byte = line >> 3;
    if (DD_LIKELY(file_id < c->cap_files)) {
        Bitset* b = &c->files[file_id];
        if (DD_LIKELY(byte < b->nbytes)) {
            b->bits[byte] |= (uint8_t)(0x80 >> (line & 7));
            if (byte >= b->used)
                b->used = byte + 1;
            return 0;
        }
    }
    return context_set_slow(c, file_id, line);
}

static inline int
context_has(const Context* c, int32_t file_id, uint32_t line)
{
    return file_id < c->cap_files && bitset_has(&c->files[file_id], line);
}

/* ------------------------------------------------------------------------------------------------
 * Re-arming.
 */
static DD_COLD int
grow_dirty(void)
{
    Py_ssize_t cap = g_cap_dirty ? g_cap_dirty * 2 : 1024;
    CodeInfo** dirty = PyMem_RawRealloc(g_dirty, sizeof(CodeInfo*) * (size_t)cap);
    if (dirty == NULL)
        return -1;
    g_dirty = dirty;
    g_cap_dirty = cap;
    return 0;
}

static inline int
mark_dirty(CodeInfo* ci)
{
    if (DD_LIKELY(ci->dirty))
        return 0;
    if (g_n_dirty == g_cap_dirty && grow_dirty() < 0)
        return -1;
    Py_INCREF(ci->code);
    g_dirty[g_n_dirty++] = ci;
    ci->dirty = 1;
    return 0;
}

static DD_COLD int
rearm_public(PyObject* code)
{
    PyObject* r = PyObject_CallFunction(g_set_local_events, "iOi", g_tool_id, code, 0);
    if (r == NULL)
        return -1;
    Py_DECREF(r);
    r = PyObject_CallFunction(g_set_local_events, "iOl", g_tool_id, code, g_line_event_mask);
    if (r == NULL)
        return -1;
    Py_DECREF(r);
    g_rearm_public++;
    return 0;
}

#if DD_HAVE_INTERNAL_REARM
/* Returns 1 when the code object can be re-armed internally, 0 when it needs the public toggle. */
static int
internal_rearm_possible(PyCodeObject* co)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (m == NULL || m->lines == NULL)
        return 0;
    if (m->per_instruction_opcodes != NULL)
        return 0; /* another tool uses INSTRUCTION events here */
    if (!(m->active_monitors.tools[g_line_event_index] & g_tool_bit))
        return 0;
    if (m->line_tools == NULL && m->active_monitors.tools[g_line_event_index] != g_tool_bit)
        return 0;
    return 1;
}

static DD_COLD int
compute_starts(CodeInfo* ci, PyCodeObject* co)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    Py_ssize_t n = Py_SIZE(co);
    int32_t count = 0;
    for (Py_ssize_t i = co->_co_firsttraceable; i < n; i++)
        if (*DD_ORIGINAL_OPCODE_PTR(m, i) != 0)
            count++;
    int32_t* starts = PyMem_RawMalloc(sizeof(int32_t) * (size_t)(count ? count : 1));
    if (starts == NULL)
        return -1;
    int32_t k = 0;
    for (Py_ssize_t i = co->_co_firsttraceable; i < n; i++)
        if (*DD_ORIGINAL_OPCODE_PTR(m, i) != 0)
            starts[k++] = (int32_t)i;
    ci->starts = starts;
    ci->n_starts = count;
    return 0;
}

/* Mirrors CPython's add_line_tools() for every line start of the code object. Returns 0 on
   success, 1 when the code object must use the public toggle instead. */
static int
rearm_internal(CodeInfo* ci, PyCodeObject* co)
{
    if (!internal_rearm_possible(co))
        return 1;
    if (ci->starts == NULL && compute_starts(ci, co) < 0)
        return 1;
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    uint8_t* bytecode = (uint8_t*)co->co_code_adaptive;
    /* Check first, so a code object is never left half re-armed. INSTRUMENTED_INSTRUCTION needs
       per-instruction opcodes, which internal_rearm_possible() ruled out, so only an interpreter
       with executors (3.13 and later) has something to find. */
    if (g_enter_executor >= 0) {
        for (int32_t k = 0; k < ci->n_starts; k++) {
            int op = bytecode[2 * ci->starts[k]];
            if (op == g_enter_executor || op == g_instrumented_instruction)
                return 1;
        }
    }
    uint8_t* line_tools = m->line_tools;
    for (int32_t k = 0; k < ci->n_starts; k++) {
        int32_t i = ci->starts[k];
        if (line_tools != NULL)
            line_tools[i] |= g_tool_bit;
        uint8_t op = bytecode[2 * i];
        if (op != g_instrumented_line) {
            *DD_ORIGINAL_OPCODE_PTR(m, i) = g_deopt[op];
            bytecode[2 * i] = (uint8_t)g_instrumented_line;
        }
    }
    g_rearm_internal++;
    g_rearm_starts += (unsigned long long)ci->n_starts;
    return 0;
}
#endif

#if DD_HAVE_INSTRUCTION_PROBES
#define DD_COMPUTED_LINE (-127) /* CPython 3.12's line delta for a line looked up in the line table */

/* Whether a LINE event can look up a line in the line table, for the instruction or the one before. */
static DD_COLD int
has_computed_lines(PyCodeObject* co)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    Py_ssize_t n = Py_SIZE(co);
    for (Py_ssize_t i = co->_co_firsttraceable; i < n; i++)
        if (m->lines[i].line_delta == DD_COMPUTED_LINE)
            return 1;
    return 0;
}

/* The line of each line start, from co_lines(), which reads the line table as LINE events do. */
static DD_COLD int
compute_start_lines(CodeInfo* ci, PyCodeObject* co)
{
    int32_t* lines = PyMem_RawMalloc(sizeof(int32_t) * (size_t)(ci->n_starts ? ci->n_starts : 1));
    if (lines == NULL)
        return -1;
    PyObject* ranges = PyObject_CallMethod((PyObject*)co, "co_lines", NULL);
    PyObject* iterator = ranges != NULL ? PyObject_GetIter(ranges) : NULL;
    Py_XDECREF(ranges);
    int32_t k = 0;
    if (iterator != NULL) {
        PyObject* item;
        while (k < ci->n_starts && (item = PyIter_Next(iterator)) != NULL) {
            long start = -1, end = -1, line = -1;
            if (PyTuple_Check(item) && PyTuple_GET_SIZE(item) == 3) {
                start = PyLong_AsLong(PyTuple_GET_ITEM(item, 0));
                end = PyLong_AsLong(PyTuple_GET_ITEM(item, 1));
                PyObject* number = PyTuple_GET_ITEM(item, 2);
                if (number != Py_None)
                    line = PyLong_AsLong(number);
            }
            Py_DECREF(item);
            if (start < 0 || end < start || PyErr_Occurred())
                break;
            /* Ranges are contiguous and in offset order, and so are the starts. */
            if (2L * ci->starts[k] < start)
                break;
            while (k < ci->n_starts && 2L * ci->starts[k] < end)
                lines[k++] = (int32_t)line;
        }
        Py_DECREF(iterator);
    }
    if (PyErr_Occurred() || k != ci->n_starts) {
        PyErr_Clear();
        PyMem_RawFree(lines);
        return -1;
    }
    PyMem_RawFree(ci->start_lines);
    ci->start_lines = lines;
    return 0;
}

/* Puts the code object back on LINE events for good, which also re-arms all its line starts. */
static DD_COLD void
revert_to_line_events(CodeInfo* ci, PyObject* code)
{
    ci->probes = PROBES_LINE;
    g_reverted++;
    PyObject* call_args[3] = { g_tool_id_object, code, g_line_mask_object };
    PyObject* r = PyObject_Vectorcall(g_set_local_events, call_args, 3, NULL);
    if (r == NULL) {
        PyErr_Clear();
        g_errors++;
        return;
    }
    Py_DECREF(r);
}

/* Decides, at the code object's first LINE event, between LINE events and INSTRUCTION probes. The
   switch needs this tool to be alone on LINE events here and nobody on INSTRUCTION events. Never
   raises. */
static DD_COLD void
choose_probes(CodeInfo* ci, PyCodeObject* co)
{
    ci->probes = PROBES_LINE;
    if (!g_instruction_probes)
        return;
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (m == NULL || m->lines == NULL || m->per_instruction_opcodes != NULL ||
        m->active_monitors.tools[g_line_event_index] != g_tool_bit ||
        m->active_monitors.tools[g_instruction_event_index] != 0 || !has_computed_lines(co))
        return;
    if (ci->starts == NULL && compute_starts(ci, co) < 0)
        return;
    if (compute_start_lines(ci, co) < 0)
        return;
    Py_ssize_t n = Py_SIZE(co);
    uint8_t* bytecode = (uint8_t*)co->co_code_adaptive;
    uint8_t* before = PyMem_RawMalloc((size_t)n);
    if (before == NULL)
        return;
    for (Py_ssize_t i = 0; i < n; i++)
        before[i] = bytecode[2 * i];
    /* Replaces this tool's LINE events with INSTRUCTION events on every instruction. */
    PyObject* call_args[3] = { g_tool_id_object, (PyObject*)co, g_instruction_mask_object };
    PyObject* r = PyObject_Vectorcall(g_set_local_events, call_args, 3, NULL);
    if (r == NULL) {
        PyErr_Clear();
        PyMem_RawFree(before);
        revert_to_line_events(ci, (PyObject*)co);
        return;
    }
    Py_DECREF(r);
    m = (dd_monitoring*)co->_co_monitoring;
    if (m->per_instruction_opcodes == NULL || m->active_monitors.tools[g_instruction_event_index] != g_tool_bit ||
        m->active_monitors.tools[g_line_event_index] != 0) {
        PyMem_RawFree(before);
        revert_to_line_events(ci, (PyObject*)co);
        return;
    }
    /* CPython allocates per_instruction_tools only once two tools share an event, and empty, which
       would silence the probes armed then. Allocated now, CPython keeps it up to date. */
    if (m->per_instruction_tools == NULL) {
        uint8_t* tools = PyMem_Malloc((size_t)n);
        if (tools == NULL) {
            PyMem_RawFree(before);
            revert_to_line_events(ci, (PyObject*)co);
            return;
        }
        memset(tools, 0, (size_t)n);
        m->per_instruction_tools = tools;
    }
    int32_t k = 0;
    for (Py_ssize_t i = co->_co_firsttraceable; i < n; i++) {
        int start = k < ci->n_starts && ci->starts[k] == i;
        if (start)
            k++;
        /* Skips what the switch left alone: RESUME, END_FOR and inline cache entries. */
        if (bytecode[2 * i] != g_instrumented_instruction || before[i] == g_instrumented_instruction)
            continue;
        if (start) {
            m->per_instruction_tools[i] |= g_tool_bit;
        } else {
            /* Back to its opcode before the switch, as if never instrumented. A superinstruction
               would run the next instruction without its probe; the first instrumentation of the
               code object removed them, so this is only a guard. */
            uint8_t op = before[i];
            bytecode[2 * i] = g_superinstruction[op] ? g_deopt[op] : op;
            m->per_instruction_opcodes[i] = 0;
            m->per_instruction_tools[i] &= (uint8_t)~g_tool_bit;
        }
    }
    PyMem_RawFree(before);
    ci->probes = PROBES_INSTRUCTION;
    g_switched++;
}

/* Mirrors CPython's add_per_instruction_tools() for every line start. Returns 0 on success, 1 when
   the code object must go back to LINE events. */
static int
rearm_instruction_probes(CodeInfo* ci, PyCodeObject* co)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (m == NULL || m->lines == NULL || m->per_instruction_opcodes == NULL || m->per_instruction_tools == NULL ||
        !(m->active_monitors.tools[g_instruction_event_index] & g_tool_bit))
        return 1;
    uint8_t* bytecode = (uint8_t*)co->co_code_adaptive;
    for (int32_t k = 0; k < ci->n_starts; k++) {
        int32_t i = ci->starts[k];
        m->per_instruction_tools[i] |= g_tool_bit;
        uint8_t* op = &bytecode[2 * i];
        if (*op == g_instrumented_line)
            op = &m->lines[i].original_opcode; /* another tool's LINE event comes first */
        if (*op == g_instrumented_instruction)
            continue;
        m->per_instruction_opcodes[i] = *op >= g_min_instrumented ? *op : g_deopt[*op];
        *op = (uint8_t)g_instrumented_instruction;
    }
    g_rearm_instruction++;
    g_rearm_starts += (unsigned long long)ci->n_starts;
    return 0;
}
#endif

/* Re-arms every listed code object. Errors are counted, never raised: a code object that cannot
   be re-armed only loses lines for later contexts. */
static void
rearm_all(void)
{
    /* The list is taken, not cleared: a re-arm can allocate, so the garbage collector can run a
       finalizer whose lines list code objects again while the loop runs. */
    CodeInfo** list = g_dirty;
    Py_ssize_t n = g_n_dirty;
    Py_ssize_t cap = g_cap_dirty;
    g_dirty = NULL;
    g_n_dirty = 0;
    g_cap_dirty = 0;
    for (Py_ssize_t k = 0; k < n; k++) {
        CodeInfo* ci = list[k];
        PyObject* code = ci->code;
        ci->dirty = 0;
        int done = 0;
#if DD_HAVE_INSTRUCTION_PROBES
        if (ci->probes == PROBES_INSTRUCTION) {
            if (rearm_instruction_probes(ci, (PyCodeObject*)code) != 0)
                revert_to_line_events(ci, code);
            done = 1;
        }
#endif
#if DD_HAVE_INTERNAL_REARM
        if (!done && g_internal_rearm && ci->rearm != 2) {
            if (rearm_internal(ci, (PyCodeObject*)code) == 0)
                done = 1;
            else
                ci->rearm = 2;
        }
#endif
        if (!done && rearm_public(code) < 0) {
            PyErr_Clear();
            g_errors++;
        }
        /* Last: the code object, and its CodeInfo with it, may go with this reference. */
        Py_DECREF(code);
    }
    if (g_dirty == NULL) {
        g_dirty = list;
        g_cap_dirty = cap;
    } else {
        PyMem_RawFree(list);
    }
}

/* ------------------------------------------------------------------------------------------------
 * The LINE and INSTRUCTION callbacks.
 */
static inline PyObject*
disable_line(CodeInfo* ci)
{
    if (mark_dirty(ci) < 0) {
        /* Keep the line armed rather than lose it for every later context. */
        g_errors++;
        Py_RETURN_NONE;
    }
    return Py_NewRef(g_disable);
}

/* A line of initialiser code: initialiser lines are process-wide, so the line is never re-armed. */
static DD_COLD PyObject*
record_init_line(CodeInfo* ci, uint32_t line)
{
    g_events_init++;
    if (bitset_set(&g_files[ci->file_id].init_lines, line) < 0) {
        g_errors++;
        Py_RETURN_NONE;
    }
    return Py_NewRef(g_disable);
}

/* The active context the running thread or task carries, or NULL. */
static inline Context*
current_owner(void)
{
    PyObject* current = NULL;
    if (DD_UNLIKELY(PyContextVar_Get(g_ctxvar, NULL, &current) < 0)) {
        PyErr_Clear();
        g_errors++;
        return NULL;
    }
    if (current == NULL)
        return NULL;
    Context* owner = Py_IS_TYPE(current, &ContextType) && ((Context*)current)->active ? (Context*)current : NULL;
    /* Borrowed from here on: the context variable keeps the object alive, and no Python code runs
       before the callback returns. */
    Py_DECREF(current);
    return owner;
}

/* Credits a line of the code object and returns what the callback returns. */
static inline PyObject*
record_line(CodeInfo* ci, uint32_t line)
{
    g_events++;
    if (DD_UNLIKELY(ci->is_init))
        return record_init_line(ci, line);
    int32_t file_id = ci->file_id;
    Context* owner = g_n_active > 0 ? current_owner() : NULL;
    if (DD_LIKELY(owner != NULL)) {
        if (DD_UNLIKELY(context_set(owner, file_id, line) < 0)) {
            g_errors++;
            Py_RETURN_NONE;
        }
    } else {
        g_events_unowned++;
        if (bitset_set(&g_files[file_id].unowned_lines, line) < 0) {
            g_errors++;
            Py_RETURN_NONE;
        }
    }
    /* Keep the line armed while an active context lacks it: it may run there next. */
    for (int k = 0; k < g_n_active; k++) {
        Context* c = g_active[k];
        if (c != owner && !context_has(c, file_id, line)) {
            g_events_kept++;
            Py_RETURN_NONE;
        }
    }
    return disable_line(ci);
}

/* A line number or an instruction offset, or -1 when the argument is neither. */
static inline long
event_number(PyObject* arg)
{
    if (DD_LIKELY(PyLong_CheckExact(arg) && PyUnstable_Long_IsCompact((PyLongObject*)arg)))
        return (long)PyUnstable_Long_CompactValue((PyLongObject*)arg);
    long value = PyLong_AsLong(arg);
    if (value == -1 && PyErr_Occurred())
        PyErr_Clear();
    return value < 0 ? -1 : value;
}

/* The callbacks are vectorcall objects rather than builtin functions: CPython then calls them
   directly, without the argument and recursion checks of a builtin function call. */
static PyObject*
line_event(PyObject* Py_UNUSED(callable), PyObject* const* args, size_t nargsf, PyObject* kwnames)
{
    if (DD_UNLIKELY(PyVectorcall_NARGS(nargsf) != 2 || kwnames != NULL))
        return Py_NewRef(g_disable);
    CodeInfo* ci = codeinfo_get(args[0]);
    if (DD_UNLIKELY(ci == NULL))
        return Py_NewRef(g_disable);
    long value = event_number(args[1]);
    if (DD_UNLIKELY(value <= 0))
        return Py_NewRef(g_disable);
#if DD_HAVE_INSTRUCTION_PROBES
    /* A switch to INSTRUCTION probes from inside this callback is safe: CPython runs the original
       opcode of this line start next, and DISABLE returned for it finds no LINE event to remove. */
    if (DD_UNLIKELY(ci->probes == PROBES_UNDECIDED))
        choose_probes(ci, (PyCodeObject*)args[0]);
#endif
    return record_line(ci, (uint32_t)value);
}

#if DD_HAVE_INSTRUCTION_PROBES
static PyObject*
instruction_event(PyObject* Py_UNUSED(callable), PyObject* const* args, size_t nargsf, PyObject* kwnames)
{
    if (DD_UNLIKELY(PyVectorcall_NARGS(nargsf) != 2 || kwnames != NULL))
        return Py_NewRef(g_disable);
    CodeInfo* ci = codeinfo_get(args[0]);
    if (DD_UNLIKELY(ci == NULL || ci->probes != PROBES_INSTRUCTION))
        return Py_NewRef(g_disable);
    long offset = event_number(args[1]);
    if (DD_UNLIKELY(offset < 0))
        return Py_NewRef(g_disable);
    int32_t index = (int32_t)(offset / 2);
    int32_t lo = 0, hi = ci->n_starts;
    while (lo < hi) {
        int32_t mid = lo + (hi - lo) / 2;
        if (ci->starts[mid] < index)
            lo = mid + 1;
        else
            hi = mid;
    }
    /* Not a line start: sys.monitoring.restart_events() re-armed every instruction. */
    if (lo == ci->n_starts || ci->starts[lo] != index || ci->start_lines[lo] <= 0)
        return Py_NewRef(g_disable);
    g_events_instruction++;
    return record_line(ci, (uint32_t)ci->start_lines[lo]);
}
#endif

typedef struct
{
    PyObject_HEAD vectorcallfunc vectorcall;
} Callback;

static PyTypeObject CallbackType = {
    PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal.coverage._linecov.Callback",
    .tp_basicsize = sizeof(Callback),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_VECTORCALL,
    .tp_vectorcall_offset = offsetof(Callback, vectorcall),
    .tp_call = PyVectorcall_Call,
    .tp_doc = "A sys.monitoring callback of the collector.",
};

static int
add_callback(PyObject* module, const char* name, vectorcallfunc function)
{
    Callback* callback = PyObject_New(Callback, &CallbackType);
    if (callback == NULL)
        return -1;
    callback->vectorcall = function;
    int rc = PyModule_AddObjectRef(module, name, (PyObject*)callback);
    Py_DECREF(callback);
    return rc;
}

/* ------------------------------------------------------------------------------------------------
 * Context type.
 */
static PyObject*
Context_new(PyTypeObject* type, PyObject* args, PyObject* kwds)
{
    static char* kwlist[] = { "fixture", NULL };
    int fixture = 0;
    /* A test context per test: skip argument parsing when there is nothing to parse. */
    if ((kwds != NULL || PyTuple_GET_SIZE(args) != 0) &&
        !PyArg_ParseTupleAndKeywords(args, kwds, "|p", kwlist, &fixture))
        return NULL;
    Context* c = (Context*)type->tp_alloc(type, 0);
    if (c == NULL)
        return NULL;
    c->kind = fixture ? CONTEXT_FIXTURE : CONTEXT_TEST;
    return (PyObject*)c;
}

static void
context_deactivate(Context* c)
{
    if (!c->active)
        return;
    Context* last = g_active[g_n_active - 1];
    g_active[c->index] = last;
    last->index = c->index;
    g_n_active--;
    c->active = 0;
    if (c->kind == CONTEXT_FIXTURE) {
        for (int32_t k = 0; k < c->n_touched; k++) {
            int32_t f = c->touched[k];
            if (bitset_or(&g_files[f].suite_lines, &c->files[f]) < 0)
                g_errors++;
        }
    }
}

static int
Context_traverse(Context* c, visitproc visit, void* arg)
{
    Py_VISIT(c->fixtures);
    return 0;
}

static int
Context_clear(Context* c)
{
    Py_CLEAR(c->fixtures);
    return 0;
}

static void
Context_dealloc(Context* c)
{
    PyObject_GC_UnTrack(c);
    context_deactivate(c);
    Context_clear(c);
    /* Only touched files have bits: context_set lists a file when it gives it bits. */
    for (int32_t k = 0; k < c->n_touched; k++)
        bitset_free(&c->files[c->touched[k]]);
    PyMem_RawFree(c->files);
    PyMem_RawFree(c->touched);
    Py_TYPE(c)->tp_free((PyObject*)c);
}

static PyObject*
Context_push(Context* c, PyObject* Py_UNUSED(ignored))
{
    if (c->active)
        Py_RETURN_NONE;
    if (g_n_active == g_cap_active) {
        int cap = g_cap_active ? g_cap_active * 2 : 16;
        Context** active = PyMem_RawRealloc(g_active, sizeof(Context*) * (size_t)cap);
        if (active == NULL)
            return PyErr_NoMemory();
        g_active = active;
        g_cap_active = cap;
    }
    rearm_all();
    c->index = g_n_active;
    g_active[g_n_active++] = c;
    c->active = 1;
    Py_RETURN_NONE;
}

static PyObject*
Context_pop(Context* c, PyObject* Py_UNUSED(ignored))
{
    context_deactivate(c);
    Py_RETURN_NONE;
}

/* enter() -> previous: push, then make this context the running one (the ctxvar init was given).
   Returns the running context it replaced, for exit(). */
static PyObject*
Context_enter(Context* c, PyObject* Py_UNUSED(ignored))
{
    if (g_ctxvar == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "_linecov.init() was not called");
        return NULL;
    }
    PyObject* previous = NULL;
    if (PyContextVar_Get(g_ctxvar, Py_None, &previous) < 0)
        return NULL;
    PyObject* r = Context_push(c, NULL);
    if (r == NULL) {
        Py_DECREF(previous);
        return NULL;
    }
    Py_DECREF(r);
    PyObject* token = PyContextVar_Set(g_ctxvar, (PyObject*)c);
    if (token == NULL) {
        context_deactivate(c);
        Py_DECREF(previous);
        return NULL;
    }
    Py_DECREF(token);
    return previous;
}

/* exit(previous): make previous the running context again, then pop. */
static PyObject*
Context_exit(Context* c, PyObject* previous)
{
    PyObject* token = PyContextVar_Set(g_ctxvar, previous);
    context_deactivate(c);
    if (token == NULL)
        return NULL;
    Py_DECREF(token);
    Py_RETURN_NONE;
}

/* The context and, transitively, the fixture contexts it depends on, each once. */
static int
collect_contexts(Context* c, Context*** out, Py_ssize_t* n, Py_ssize_t* cap)
{
    for (Py_ssize_t k = 0; k < *n; k++)
        if ((*out)[k] == c)
            return 0;
    if (*n == *cap) {
        Py_ssize_t size = *cap ? *cap * 2 : 8;
        Context** grown = PyMem_RawRealloc(*out, sizeof(Context*) * (size_t)size);
        if (grown == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        *out = grown;
        *cap = size;
    }
    (*out)[(*n)++] = c;
    if (c->fixtures == NULL)
        return 0;
    Py_ssize_t count = PyList_GET_SIZE(c->fixtures);
    for (Py_ssize_t k = 0; k < count; k++) {
        PyObject* item = PyList_GET_ITEM(c->fixtures, k);
        if (Py_IS_TYPE(item, &ContextType) && collect_contexts((Context*)item, out, n, cap) < 0)
            return -1;
    }
    return 0;
}

/* bitmaps(include_init=True) -> list of (report_path, bytes) for files in the workspace: the
   context's lines, the lines of the fixture contexts it depends on, and, with include_init, the
   initialiser lines of every file any of them touched. */
static PyObject*
Context_bitmaps(Context* c, PyObject* const* args, Py_ssize_t nargs)
{
    int include_init = 1;
    if (nargs > 0 && (include_init = PyObject_IsTrue(args[0])) < 0)
        return NULL;
    Context** ctxs = NULL;
    Py_ssize_t n_ctxs = 0, cap_ctxs = 0;
    uint8_t* seen = NULL;
    PyObject* result = NULL;
    if (collect_contexts(c, &ctxs, &n_ctxs, &cap_ctxs) < 0)
        goto done;
    seen = PyMem_RawCalloc(g_n_files > 0 ? (size_t)g_n_files : 1, 1);
    if (seen == NULL) {
        PyErr_NoMemory();
        goto done;
    }
    result = PyList_New(0);
    if (result == NULL)
        goto done;
    for (Py_ssize_t j = 0; j < n_ctxs; j++) {
        Context* source = ctxs[j];
        for (int32_t k = 0; k < source->n_touched; k++) {
            int32_t f = source->touched[k];
            if (seen[f])
                continue;
            seen[f] = 1;
            FileInfo* fi = &g_files[f];
            if (fi->report_path == Py_None)
                continue;
            uint32_t n = include_init ? bitset_used(&fi->init_lines) : 0;
            for (Py_ssize_t i = 0; i < n_ctxs; i++) {
                if (f < ctxs[i]->cap_files) {
                    uint32_t used = bitset_used(&ctxs[i]->files[f]);
                    if (used > n)
                        n = used;
                }
            }
            PyObject* bytes = PyBytes_FromStringAndSize(NULL, n);
            if (bytes == NULL)
                goto error;
            uint8_t* out = (uint8_t*)PyBytes_AS_STRING(bytes);
            memset(out, 0, n);
            if (include_init) {
                uint32_t used = bitset_used(&fi->init_lines);
                for (uint32_t b = 0; b < used; b++)
                    out[b] |= fi->init_lines.bits[b];
            }
            for (Py_ssize_t i = 0; i < n_ctxs; i++) {
                if (f >= ctxs[i]->cap_files)
                    continue;
                const Bitset* bits = &ctxs[i]->files[f];
                uint32_t used = bitset_used(bits);
                for (uint32_t b = 0; b < used; b++)
                    out[b] |= bits->bits[b];
            }
            PyObject* item = PyTuple_Pack(2, fi->report_path, bytes);
            Py_DECREF(bytes);
            if (item == NULL || PyList_Append(result, item) < 0) {
                Py_XDECREF(item);
                goto error;
            }
            Py_DECREF(item);
        }
    }
    goto done;
error:
    Py_CLEAR(result);
done:
    PyMem_RawFree(ctxs);
    PyMem_RawFree(seen);
    return result;
}

/* covered() -> dict of absolute path -> bytearray, the context's own lines only. */
static PyObject*
Context_covered(Context* c, PyObject* Py_UNUSED(ignored))
{
    PyObject* result = PyDict_New();
    if (result == NULL)
        return NULL;
    for (int32_t k = 0; k < c->n_touched; k++) {
        int32_t f = c->touched[k];
        Bitset* b = &c->files[f];
        PyObject* ba = PyByteArray_FromStringAndSize((const char*)b->bits, bitset_used(b));
        if (ba == NULL || PyDict_SetItem(result, g_files[f].path, ba) < 0) {
            Py_XDECREF(ba);
            Py_DECREF(result);
            return NULL;
        }
        Py_DECREF(ba);
    }
    return result;
}

static PyObject*
Context_get_active(Context* c, void* Py_UNUSED(closure))
{
    return PyBool_FromLong(c->active);
}

static PyObject*
Context_get_is_fixture(Context* c, void* Py_UNUSED(closure))
{
    return PyBool_FromLong(c->kind == CONTEXT_FIXTURE);
}

static PyObject*
Context_get_fixtures(Context* c, void* Py_UNUSED(closure))
{
    if (c->fixtures == NULL && (c->fixtures = PyList_New(0)) == NULL)
        return NULL;
    return Py_NewRef(c->fixtures);
}

static PyMethodDef Context_methods[] = {
    { "push", (PyCFunction)Context_push, METH_NOARGS, "Activate, re-arming disabled lines." },
    { "pop", (PyCFunction)Context_pop, METH_NOARGS, "Deactivate." },
    { "enter", (PyCFunction)Context_enter, METH_NOARGS, "Push and run in this context; returns the previous one." },
    { "exit", (PyCFunction)Context_exit, METH_O, "Run in the given previous context again, and pop." },
    { "bitmaps", (PyCFunction)(void (*)(void))Context_bitmaps, METH_FASTCALL, "Record bitmaps." },
    { "covered", (PyCFunction)Context_covered, METH_NOARGS, "Own lines by absolute path." },
    { NULL, NULL, 0, NULL },
};

static PyGetSetDef Context_getset[] = {
    { "active", (getter)Context_get_active, NULL, NULL, NULL },
    { "is_fixture", (getter)Context_get_is_fixture, NULL, NULL, NULL },
    { "fixtures", (getter)Context_get_fixtures, NULL, "Fixture contexts this one depends on.", NULL },
    { NULL, NULL, NULL, NULL, NULL },
};

static PyTypeObject ContextType = {
    PyVarObject_HEAD_INIT(NULL, 0).tp_name = "ddtrace.internal.coverage._linecov.Context",
    .tp_basicsize = sizeof(Context),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .tp_new = Context_new,
    .tp_dealloc = (destructor)Context_dealloc,
    .tp_traverse = (traverseproc)Context_traverse,
    .tp_clear = (inquiry)Context_clear,
    .tp_methods = Context_methods,
    .tp_getset = Context_getset,
};

/* ------------------------------------------------------------------------------------------------
 * Module functions.
 */
static PyObject*
mod_init(PyObject* Py_UNUSED(self), PyObject* args, PyObject* kwds)
{
    static char* kwlist[] = { "tool_id",
                              "line_event_index",
                              "disable",
                              "set_local_events",
                              "ctxvar",
                              "deopt",
                              "instrumented_line",
                              "enter_executor",
                              "instrumented_instruction",
                              "internal_rearm",
                              "instruction_event_index",
                              "min_instrumented",
                              "superinstructions",
                              "instruction_probes",
                              NULL };
    int tool_id, line_event_index, instrumented_line, enter_executor, instrumented_instruction;
    int internal_rearm = 1, instruction_event_index = -1, min_instrumented = 256, instruction_probes = 0;
    PyObject *disable, *set_local_events, *ctxvar, *superinstructions = NULL;
    Py_buffer deopt;
    if (!PyArg_ParseTupleAndKeywords(args,
                                     kwds,
                                     "iiOOOy*iii|piiOp",
                                     kwlist,
                                     &tool_id,
                                     &line_event_index,
                                     &disable,
                                     &set_local_events,
                                     &ctxvar,
                                     &deopt,
                                     &instrumented_line,
                                     &enter_executor,
                                     &instrumented_instruction,
                                     &internal_rearm,
                                     &instruction_event_index,
                                     &min_instrumented,
                                     &superinstructions,
                                     &instruction_probes))
        return NULL;
    if (deopt.len != 256) {
        PyBuffer_Release(&deopt);
        PyErr_SetString(PyExc_ValueError, "deopt must be 256 bytes");
        return NULL;
    }
    if (!PyContextVar_CheckExact(ctxvar)) {
        PyBuffer_Release(&deopt);
        PyErr_SetString(PyExc_TypeError, "ctxvar must be a ContextVar");
        return NULL;
    }
    if (superinstructions != NULL && superinstructions != Py_None &&
        (!PyBytes_Check(superinstructions) || PyBytes_GET_SIZE(superinstructions) != 256)) {
        PyBuffer_Release(&deopt);
        PyErr_SetString(PyExc_ValueError, "superinstructions must be 256 bytes");
        return NULL;
    }
    memcpy(g_deopt, deopt.buf, 256);
    PyBuffer_Release(&deopt);
    if (superinstructions != NULL && superinstructions != Py_None)
        memcpy(g_superinstruction, PyBytes_AS_STRING(superinstructions), 256);
    else
        memset(g_superinstruction, 0, 256);
    PyObject* instruction_mask_object = NULL;
    if (instruction_event_index >= 0 &&
        (instruction_mask_object = PyLong_FromLong(1L << instruction_event_index)) == NULL)
        return NULL;
    Py_XSETREF(g_instruction_mask_object, instruction_mask_object);
    if (g_extra_index < 0) {
        g_extra_index = PyUnstable_Eval_RequestCodeExtraIndex(codeinfo_free);
        if (g_extra_index < 0) {
            PyErr_SetString(PyExc_RuntimeError, "no code extra index available");
            return NULL;
        }
    }
    PyObject* tool_id_object = PyLong_FromLong(tool_id);
    PyObject* line_mask_object = PyLong_FromLong(1L << line_event_index);
    PyObject* generic_scope_prefix = PyUnicode_FromString("<generic parameters of ");
    if (tool_id_object == NULL || line_mask_object == NULL || generic_scope_prefix == NULL) {
        Py_XDECREF(tool_id_object);
        Py_XDECREF(line_mask_object);
        Py_XDECREF(generic_scope_prefix);
        return NULL;
    }
    Py_XSETREF(g_tool_id_object, tool_id_object);
    Py_XSETREF(g_line_mask_object, line_mask_object);
    Py_XSETREF(g_generic_scope_prefix, generic_scope_prefix);
    g_tool_id = tool_id;
    g_tool_bit = (uint8_t)(1 << tool_id);
    g_line_event_index = line_event_index;
    g_line_event_mask = 1L << line_event_index;
    Py_INCREF(disable);
    Py_XSETREF(g_disable, disable);
    Py_INCREF(set_local_events);
    Py_XSETREF(g_set_local_events, set_local_events);
    Py_INCREF(ctxvar);
    Py_XSETREF(g_ctxvar, ctxvar);
    g_instrumented_line = instrumented_line;
    g_enter_executor = enter_executor;
    g_instrumented_instruction = instrumented_instruction;
    g_instruction_event_index = instruction_event_index;
    g_min_instrumented = min_instrumented;
    g_internal_rearm = DD_HAVE_INTERNAL_REARM && internal_rearm && instrumented_line > 0;
    g_instruction_probes = DD_HAVE_INSTRUCTION_PROBES && g_internal_rearm && instruction_probes &&
                           instruction_event_index >= 0 && instrumented_instruction > 0 && min_instrumented < 256;
    g_initialized = 1;
    return PyBool_FromLong(g_internal_rearm);
}

/* The probes rely on the internal re-arm, so turning it off turns them off for code objects not
   decided yet. */
static PyObject*
mod_set_internal_rearm(PyObject* Py_UNUSED(self), PyObject* arg)
{
    int on = PyObject_IsTrue(arg);
    if (on < 0)
        return NULL;
    g_internal_rearm = DD_HAVE_INTERNAL_REARM && on && g_instrumented_line > 0;
    if (!g_internal_rearm)
        g_instruction_probes = 0;
    return PyBool_FromLong(g_internal_rearm);
}

/* Switches INSTRUCTION probes for code objects not decided yet. */
static PyObject*
mod_set_instruction_probes(PyObject* Py_UNUSED(self), PyObject* arg)
{
    int on = PyObject_IsTrue(arg);
    if (on < 0)
        return NULL;
    g_instruction_probes = DD_HAVE_INSTRUCTION_PROBES && on && g_internal_rearm && g_instruction_event_index >= 0 &&
                           g_instrumented_instruction > 0 && g_min_instrumented < 256;
    return PyBool_FromLong(g_instruction_probes);
}

/* probes(code) -> 0 when not decided or not attached, 1 for LINE events, 2 for INSTRUCTION probes. */
static PyObject*
mod_probes(PyObject* Py_UNUSED(self), PyObject* code)
{
    if (!PyCode_Check(code)) {
        PyErr_SetString(PyExc_TypeError, "probes(code)");
        return NULL;
    }
    CodeInfo* ci = codeinfo_get(code);
    return PyLong_FromLong(ci != NULL ? ci->probes : PROBES_UNDECIDED);
}

static PyObject*
mod_register_file(PyObject* Py_UNUSED(self), PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "register_file(path, report_path)");
        return NULL;
    }
    if (g_n_files == g_cap_files) {
        int32_t cap = g_cap_files ? g_cap_files * 2 : 256;
        FileInfo* files = PyMem_RawRealloc(g_files, sizeof(FileInfo) * (size_t)cap);
        if (files == NULL)
            return PyErr_NoMemory();
        memset(files + g_cap_files, 0, sizeof(FileInfo) * (size_t)(cap - g_cap_files));
        g_files = files;
        g_cap_files = cap;
    }
    FileInfo* fi = &g_files[g_n_files];
    fi->path = Py_NewRef(args[0]);
    fi->report_path = Py_NewRef(args[1]);
    return PyLong_FromLong(g_n_files++);
}

/* Attaches a code object to a file. is_init < 0 keeps an existing initialiser mark. */
static int
attach_code(PyObject* code, int32_t file_id, int is_init)
{
    CodeInfo* ci = codeinfo_get(code);
    if (ci == NULL) {
        ci = PyMem_RawCalloc(1, sizeof(CodeInfo));
        if (ci == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        ci->code = code;
        if (PyUnstable_Code_SetExtra(code, g_extra_index, ci) < 0) {
            PyMem_RawFree(ci);
            return -1;
        }
    }
    ci->file_id = file_id;
    if (is_init >= 0)
        ci->is_init = (uint8_t)is_init;
    return 0;
}

static int
parse_file_id(PyObject* arg, int32_t* file_id)
{
    long value = PyLong_AsLong(arg);
    if (value < 0 || value >= g_n_files) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_ValueError, "unknown file id");
        return -1;
    }
    *file_id = (int32_t)value;
    return 0;
}

/* attach(code, file_id[, is_init]): without is_init, an existing mark is kept. */
static PyObject*
mod_attach(PyObject* Py_UNUSED(self), PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs < 2 || nargs > 3 || !PyCode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "attach(code, file_id, is_init=False)");
        return NULL;
    }
    int32_t file_id;
    if (parse_file_id(args[1], &file_id) < 0)
        return NULL;
    int is_init = -1;
    if (nargs == 3 && (is_init = PyObject_IsTrue(args[2])) < 0)
        return NULL;
    if (attach_code(args[0], file_id, is_init) < 0)
        return NULL;
    Py_RETURN_NONE;
}

/* ------------------------------------------------------------------------------------------------
 * Instrumenting a module.
 */
#define DD_CO_OPTIMIZED 0x0001

/* Constants the compiler emits. None of them can come from an obfuscator's runtime. */
static inline int
is_literal_constant(PyObject* c)
{
    return c == Py_None || c == Py_Ellipsis || PyBool_Check(c) || PyCode_Check(c) || PyUnicode_CheckExact(c) ||
           PyLong_CheckExact(c) || PyTuple_CheckExact(c) || PyBytes_CheckExact(c) || PyFloat_CheckExact(c) ||
           PyFrozenSet_CheckExact(c) || PyComplex_CheckExact(c) || PySlice_Check(c);
}

static int
has_prefix(PyObject* text, const char* prefix)
{
    Py_ssize_t size = 0;
    const char* utf8 = PyUnicode_AsUTF8AndSize(text, &size);
    if (utf8 == NULL) {
        PyErr_Clear();
        return 0;
    }
    size_t n = strlen(prefix);
    return (size_t)size >= n && memcmp(utf8, prefix, n) == 0;
}

/* Mirrors ddtrace.internal.utils.obfuscation._is_runtime_object. */
static int
is_obfuscator_runtime_object(PyObject* c)
{
    PyObject* module = PyObject_GetAttrString(c, "__module__");
    if (module == NULL) {
        PyErr_Clear();
        return 0;
    }
    if (PyModule_Check(module)) {
        PyObject* name = PyModule_GetNameObject(module);
        Py_DECREF(module);
        if (name == NULL) {
            PyErr_Clear();
            return 0;
        }
        module = name;
    }
    int found = PyUnicode_Check(module) && (has_prefix(module, "pytransform") || has_prefix(module, "pyarmor_runtime"));
    Py_DECREF(module);
    return found;
}

/* Mirrors ddtrace.internal.utils.obfuscation.is_obfuscated_code. */
static int
is_obfuscated(PyCodeObject* co)
{
    PyObject* names = co->co_names;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(names); i++) {
        PyObject* name = PyTuple_GET_ITEM(names, i);
        Py_ssize_t n = PyUnicode_GET_LENGTH(name);
        if ((n == 15 && PyUnicode_CompareWithASCIIString(name, "__armor_enter__") == 0) ||
            (n == 14 && PyUnicode_CompareWithASCIIString(name, "__armor_exit__") == 0))
            return 1;
    }
    PyObject* consts = co->co_consts;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(consts); i++) {
        PyObject* c = PyTuple_GET_ITEM(consts, i);
        if (!is_literal_constant(c) && is_obfuscator_runtime_object(c))
            return 1;
    }
    return 0;
}

/* Class bodies are the only unoptimized code objects nested in initialiser code. A generic class
   or function first runs a type-parameter scope at definition time, with the class body nested
   in it. */
static int
is_initialiser_child(PyCodeObject* co)
{
    if (!(co->co_flags & DD_CO_OPTIMIZED))
        return 1;
    Py_ssize_t match = PyUnicode_Tailmatch(co->co_name, g_generic_scope_prefix, 0, PY_SSIZE_T_MAX, -1);
    if (match < 0) {
        PyErr_Clear();
        return 0;
    }
    return match == 1;
}

typedef struct
{
    PyCodeObject* code; /* borrowed: the module code object owns the tree */
    uint8_t is_init;
} PendingCode;

/* instrument(module_code, file_id) -> int: enables LINE events on every code object of the tree,
   except obfuscated ones and the code nested in them, and attaches each to the file. The module
   body and the class bodies nested statically in it are marked as initialiser code. Returns how
   many code objects were instrumented. A code object that cannot be instrumented is counted as
   an error and skipped, never raised. */
static PyObject*
mod_instrument(PyObject* Py_UNUSED(self), PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2 || !PyCode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "instrument(code, file_id)");
        return NULL;
    }
    if (!g_initialized) {
        PyErr_SetString(PyExc_RuntimeError, "not initialised");
        return NULL;
    }
    int32_t file_id;
    if (parse_file_id(args[1], &file_id) < 0)
        return NULL;

    PyCodeObject* root = (PyCodeObject*)args[0];
    if (is_obfuscated(root))
        return PyLong_FromLong(-1);
    Py_ssize_t cap = 64, n = 0;
    PendingCode* pending = PyMem_RawMalloc(sizeof(PendingCode) * (size_t)cap);
    if (pending == NULL)
        return PyErr_NoMemory();
    pending[n].code = root;
    pending[n].is_init = PyUnicode_CompareWithASCIIString(root->co_name, "<module>") == 0;
    n++;

    long instrumented = 0;
    while (n > 0) {
        PendingCode item = pending[--n];
        PyCodeObject* co = item.code;
        if (is_obfuscated(co))
            continue;
        PyObject* call_args[3] = { g_tool_id_object, (PyObject*)co, g_line_mask_object };
        PyObject* r = PyObject_Vectorcall(g_set_local_events, call_args, 3, NULL);
        if (r == NULL) {
            PyErr_Clear();
            g_errors++;
            continue;
        }
        Py_DECREF(r);
        if (attach_code((PyObject*)co, file_id, item.is_init) < 0) {
            PyErr_Clear();
            g_errors++;
            continue;
        }
        instrumented++;
        PyObject* consts = co->co_consts;
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(consts); i++) {
            PyObject* c = PyTuple_GET_ITEM(consts, i);
            if (!PyCode_Check(c))
                continue;
            if (n == cap) {
                PendingCode* grown = PyMem_RawRealloc(pending, sizeof(PendingCode) * (size_t)(cap * 2));
                if (grown == NULL) {
                    g_errors++;
                    break;
                }
                pending = grown;
                cap *= 2;
            }
            pending[n].code = (PyCodeObject*)c;
            pending[n].is_init = item.is_init && is_initialiser_child((PyCodeObject*)c);
            n++;
        }
    }
    PyMem_RawFree(pending);
    return PyLong_FromLong(instrumented);
}

static PyObject*
mod_rearm(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    rearm_all();
    Py_RETURN_NONE;
}

static int
append_file_bitmap(PyObject* list, FileInfo* fi, const Bitset* b)
{
    uint32_t used = bitset_used(b);
    if (used == 0 || fi->report_path == Py_None)
        return 0;
    PyObject* bytes = PyBytes_FromStringAndSize((const char*)b->bits, used);
    if (bytes == NULL)
        return -1;
    PyObject* item = PyTuple_Pack(2, fi->report_path, bytes);
    Py_DECREF(bytes);
    if (item == NULL)
        return -1;
    int rc = PyList_Append(list, item);
    Py_DECREF(item);
    return rc;
}

/* shared() -> {"init": [...], "suiteSetup": [...], "unowned": [...]}, each a list of
   (report_path, bytes) for files in the workspace. */
static PyObject*
mod_shared(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    PyObject* init = PyList_New(0);
    PyObject* suite = PyList_New(0);
    PyObject* unowned = PyList_New(0);
    PyObject* result = NULL;
    if (init == NULL || suite == NULL || unowned == NULL)
        goto done;
    for (int32_t f = 0; f < g_n_files; f++) {
        FileInfo* fi = &g_files[f];
        if (append_file_bitmap(init, fi, &fi->init_lines) < 0 || append_file_bitmap(suite, fi, &fi->suite_lines) < 0 ||
            append_file_bitmap(unowned, fi, &fi->unowned_lines) < 0)
            goto done;
    }
    result = Py_BuildValue("{sOsOsO}", "init", init, "suiteSetup", suite, "unowned", unowned);
done:
    Py_XDECREF(init);
    Py_XDECREF(suite);
    Py_XDECREF(unowned);
    return result;
}

static PyObject*
mod_stats(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    return Py_BuildValue("{s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:K,s:n,s:i,s:i,s:i,s:i}",
                         "events",
                         g_events,
                         "events_init",
                         g_events_init,
                         "events_unowned",
                         g_events_unowned,
                         "events_kept",
                         g_events_kept,
                         "events_instruction",
                         g_events_instruction,
                         "errors",
                         g_errors,
                         "rearm_internal",
                         g_rearm_internal,
                         "rearm_public",
                         g_rearm_public,
                         "rearm_instruction",
                         g_rearm_instruction,
                         "rearm_starts",
                         g_rearm_starts,
                         "switched",
                         g_switched,
                         "reverted",
                         g_reverted,
                         "dirty",
                         g_n_dirty,
                         "active",
                         g_n_active,
                         "files",
                         g_n_files,
                         "internal_rearm",
                         g_internal_rearm,
                         "instruction_probes",
                         g_instruction_probes);
}

static PyMethodDef module_methods[] = {
    { "init", (PyCFunction)(void (*)(void))mod_init, METH_VARARGS | METH_KEYWORDS, "Configure the collector." },
    { "set_internal_rearm", mod_set_internal_rearm, METH_O, "Switch the internal re-arm." },
    { "set_instruction_probes", mod_set_instruction_probes, METH_O, "Switch INSTRUCTION probes." },
    { "probes", mod_probes, METH_O, "The kind of probes a code object uses." },
    { "register_file", (PyCFunction)(void (*)(void))mod_register_file, METH_FASTCALL, "Register a file." },
    { "attach", (PyCFunction)(void (*)(void))mod_attach, METH_FASTCALL, "Attach a code object to a file." },
    { "instrument",
      (PyCFunction)(void (*)(void))mod_instrument,
      METH_FASTCALL,
      "Enable LINE events on a module's code tree and attach it to a file. Returns the number of "
      "code objects instrumented, or -1 when the module body looks obfuscated." },
    { "rearm", mod_rearm, METH_NOARGS, "Re-arm every disabled line start." },
    { "shared", mod_shared, METH_NOARGS, "The process's Shared record, in three parts." },
    { "stats", mod_stats, METH_NOARGS, "Counters." },
    { NULL, NULL, 0, NULL },
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT, "_linecov", NULL, -1, module_methods, NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC
PyInit__linecov(void)
{
    if (PyType_Ready(&ContextType) < 0 || PyType_Ready(&CallbackType) < 0)
        return NULL;
    PyObject* m = PyModule_Create(&module_def);
    if (m == NULL)
        return NULL;
    Py_INCREF(&ContextType);
    if (PyModule_AddObject(m, "Context", (PyObject*)&ContextType) < 0) {
        Py_DECREF(&ContextType);
        Py_DECREF(m);
        return NULL;
    }
    /* The sys.monitoring LINE and INSTRUCTION callbacks. */
    if (add_callback(m, "line_event", line_event) < 0
#if DD_HAVE_INSTRUCTION_PROBES
        || add_callback(m, "instruction_event", instruction_event) < 0
#endif
    ) {
        Py_DECREF(m);
        return NULL;
    }
    if (PyModule_AddIntConstant(m, "HAVE_INTERNAL_REARM", DD_HAVE_INTERNAL_REARM) < 0 ||
        PyModule_AddIntConstant(m, "HAVE_INSTRUCTION_PROBES", DD_HAVE_INSTRUCTION_PROBES) < 0 ||
        PyModule_AddIntConstant(m, "FREE_THREADED", DD_LINECOV_FREE_THREADED) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
