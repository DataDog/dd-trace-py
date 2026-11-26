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
 * Child processes: a Python child that a context starts records the lines it runs and hands them
 * to this process in a record file at exit (see child_bootstrap/_ddtrace_child_coverage.py). A state
 * file, mapped in both processes, carries the token of the context this process runs for, which
 * the child reads at start-up, and a count of the records written, so this process reads the
 * record directory only after a child wrote to it. A record joins the context while it is still
 * active, and the unowned lines once its window has closed. A multiprocessing child forked from
 * this process calls forget() and then records as such a child does.
 *
 * Owner lookup: PyContextVar_Get() first reads a one-value cache that CPython keeps in the context
 * variable, valid while the thread that filled it runs the same context. The callbacks read that
 * cache themselves and call PyContextVar_Get() when it does not hold the value, which saves a call
 * per event. A self-test at init compares both in a temporary context and keeps the call if they
 * ever disagree.
 *
 * The callbacks never raise into the monitored code: an internal error is counted and the line
 * stays armed. All state is guarded by the GIL. Free-threaded builds are not supported.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifndef _WIN32
#include <fcntl.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
#define DD_HAVE_SHARED_STATE 1
#else
#define DD_HAVE_SHARED_STATE 0
#endif

#if PY_VERSION_HEX < 0x030C0000
#error "_linecov requires CPython 3.12 or later"
#endif

#ifdef Py_GIL_DISABLED
#define DD_LINECOV_FREE_THREADED 1
#else
#define DD_LINECOV_FREE_THREADED 0
#endif

#if PY_VERSION_HEX >= 0x030D0000
#define DD_THREAD_STATE() PyThreadState_GetUnchecked()
#else
#define DD_THREAD_STATE() _PyThreadState_UncheckedGet()
#endif

/* ------------------------------------------------------------------------------------------------
 * CPython's context variable object, as Include/internal/pycore_context.h declares it in 3.12 to
 * 3.14. Free-threaded builds have no value cache. The init self-test turns the cache read off if
 * the layout or the cache rules differ.
 */
#if !DD_LINECOV_FREE_THREADED && PY_VERSION_HEX < 0x030F0000
#define DD_HAVE_CACHED_OWNER 1
typedef struct
{
    PyObject_HEAD PyObject* var_name;
    PyObject* var_default;
    PyObject* var_cached; /* borrowed from the context that holds it */
    uint64_t var_cached_tsid;
    uint64_t var_cached_tsver;
    Py_hash_t var_hash;
} dd_context_var;
#else
#define DD_HAVE_CACHED_OWNER 0
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

/* Block probes read CPython 3.12's interpreter frames, declared in an internal header. */
#if DD_HAVE_INSTRUCTION_PROBES && !defined(_WIN32)
#define DD_HAVE_BLOCK_PROBES 1
#ifndef Py_BUILD_CORE
#define Py_BUILD_CORE 1
#endif
#include "internal/pycore_frame.h"
#else
#define DD_HAVE_BLOCK_PROBES 0
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
    PROBES_BLOCK = 3,       /* INSTRUCTION events on the block probes, see "Block probes" */
};

typedef struct BlockInfo BlockInfo;

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
#if DD_HAVE_BLOCK_PROBES
    BlockInfo* blocks;     /* set while probes is PROBES_BLOCK */
    Py_ssize_t block_slot; /* position in g_block_codes while probes is PROBES_BLOCK */
    uint8_t settling;      /* listed in g_settle by a frame walk */
#endif
} CodeInfo;

#if DD_HAVE_BLOCK_PROBES
/* The blocks of a code object with block probes, and where its probes are. One allocation. */
struct BlockInfo
{
    int32_t n_code; /* code units */
    int32_t n_blocks;
    int32_t n_probes;
    int32_t n_ranges;      /* exception table entries */
    uint64_t* token;       /* per block: the context credited with the block and its dominators */
    int32_t* first;        /* per block, ascending, then n_code: the block's first code unit */
    int32_t* idom;         /* per block: its immediate dominator, or -1 when only the entry dominates it */
    int32_t* start_lo;     /* per block, then one more: its line starts are starts[start_lo[b]:start_lo[b + 1]] */
    int32_t* yield_at;     /* per block: the YIELD_VALUE that ends it, or -1 */
    int32_t* probe_index;  /* per probe, ascending: its code unit */
    int32_t* probe_block;  /* per probe: the block it credits, or -1 */
    uint8_t* probe_caught; /* per probe: 1 for a probe after a handler's PUSH_EXC_INFO */
    int32_t* range_start;  /* per exception table entry, ascending */
    int32_t* range_end;
    int32_t* range_catch; /* per entry: the handler that catches what is raised in it, or -1 */
};

/* Token of a block credited to the unowned lines; 0 means never credited. */
#define DD_TOKEN_UNOWNED UINT64_MAX
#endif

static Py_ssize_t g_extra_index = -1;

#if DD_HAVE_BLOCK_PROBES
static void
drop_blocks(CodeInfo* ci);
#endif

static void
codeinfo_free(void* p)
{
    CodeInfo* ci = (CodeInfo*)p;
    if (ci == NULL)
        return;
#if DD_HAVE_BLOCK_PROBES
    drop_blocks(ci);
#endif
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
    PyObject* fixtures; /* list of the contexts whose lines this one's record includes, or NULL */
    int kind;
    int active;
    int index;      /* position in g_active while active */
    uint64_t token; /* names the context to child processes; unique in the process */
    /* A window entered over this one, on a thread that ran for it, suspends it until that window
       closes: this context keeps no lines armed meanwhile, and the close re-arms. Another thread
       still running for it misses the lines the inner context ran first, so callers nest windows
       only where the inner context's lines count for this one too. */
    int suspended;         /* inner windows open over this one */
    struct Context* outer; /* the context this one's open window suspends, or NULL */
    PyObject* previous;    /* the running context __enter__ replaced, for __exit__; NULL when not entered */
} Context;

static PyTypeObject ContextType;

static Context** g_active = NULL;
static int g_n_active = 0;
static int g_cap_active = 0;
static uint64_t g_next_token = 1; /* 0 names no context */

/* The state file shared with child processes, mapped while child processes are followed. */
typedef struct
{
    uint64_t owner;   /* token of the context this process runs for, 0 for none */
    uint64_t records; /* child records written so far */
} SharedState;

static SharedState* g_state = NULL;

/* What a window's __exit__ calls when child processes wrote records since it last ran: it merges them
   into their contexts. g_records_seen is the record count at that last run. */
static PyObject* g_records_callback = NULL;
static uint64_t g_records_seen = 0;

/* Configuration, set by init(). */
static int g_initialized = 0;
static int g_child = 0; /* set up by a child process bootstrap, which keeps the collector */
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
static int g_cached_owner = 0; /* the callbacks read the context variable's cache */
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
static int g_block_probes = 0;
static int g_block_tables = 0;       /* init() was given the two tables below */
static uint8_t g_block_kind[256];    /* BK_* of each base opcode */
static uint8_t g_cache_entries[256]; /* inline cache entries after each base opcode */

/* Counters, for profiling and tests. Owned line events have none: the count would cost every event. */
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
static unsigned long long g_events_block = 0;   /* block probe events */
static unsigned long long g_events_caught = 0;  /* handler probe events */
static unsigned long long g_events_unwind = 0;  /* PY_UNWIND events in code with block probes */
static unsigned long long g_switched_block = 0; /* code objects moved to block probes */
static unsigned long long g_block_refused = 0;  /* code objects the block analysis could not take */
static unsigned long long g_converted = 0;      /* code objects moved from block probes to line-start probes */
static unsigned long long g_rearm_block = 0;

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

/* The line of each line start, read from the line table by _PyCode_CheckLineNumber(), the function
   LINE events use. The address range is set up as _PyLineTable_InitAddressRange() does, which CPython
   does not export. Starts are ascending, so the walk reads the table once. */
static DD_COLD int
compute_start_lines(CodeInfo* ci, PyCodeObject* co)
{
    int32_t* lines = PyMem_RawMalloc(sizeof(int32_t) * (size_t)(ci->n_starts ? ci->n_starts : 1));
    if (lines == NULL)
        return -1;
    PyCodeAddressRange range;
    range.opaque.lo_next = (const uint8_t*)PyBytes_AS_STRING(co->co_linetable);
    range.opaque.limit = range.opaque.lo_next + PyBytes_GET_SIZE(co->co_linetable);
    range.ar_start = -1;
    range.ar_end = 0;
    range.opaque.computed_line = co->co_firstlineno;
    range.ar_line = -1;
    for (int32_t k = 0; k < ci->n_starts; k++) {
        int line = _PyCode_CheckLineNumber(ci->starts[k] * (int)sizeof(_Py_CODEUNIT), &range);
        if (line < 0 && range.ar_end <= ci->starts[k] * (int)sizeof(_Py_CODEUNIT)) {
            PyMem_RawFree(lines);
            return -1; /* past the end of the table */
        }
        lines[k] = line;
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

/* Whether this tool's LINE events on the code object can become INSTRUCTION events: it must be alone
   on LINE events there, and nobody on INSTRUCTION events. */
static int
can_switch_to_instruction_events(PyCodeObject* co)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    return m != NULL && m->lines != NULL && m->per_instruction_opcodes == NULL &&
           m->active_monitors.tools[g_line_event_index] == g_tool_bit &&
           m->active_monitors.tools[g_instruction_event_index] == 0;
}

/* Replaces this tool's LINE events on the code object with INSTRUCTION events on the given
   instructions only, in ascending order. Returns 0 on success. On failure the code object is back on
   LINE events, with probes set to PROBES_LINE. Never raises. */
static DD_COLD int
switch_to_instruction_events(CodeInfo* ci, PyCodeObject* co, const int32_t* positions, int32_t n_positions)
{
    Py_ssize_t n = Py_SIZE(co);
    uint8_t* bytecode = (uint8_t*)co->co_code_adaptive;
    uint8_t* before = PyMem_RawMalloc((size_t)n);
    if (before == NULL)
        return -1;
    for (Py_ssize_t i = 0; i < n; i++)
        before[i] = bytecode[2 * i];
    /* Replaces this tool's LINE events with INSTRUCTION events on every instruction. */
    PyObject* call_args[3] = { g_tool_id_object, (PyObject*)co, g_instruction_mask_object };
    PyObject* r = PyObject_Vectorcall(g_set_local_events, call_args, 3, NULL);
    if (r == NULL) {
        PyErr_Clear();
        PyMem_RawFree(before);
        revert_to_line_events(ci, (PyObject*)co);
        return -1;
    }
    Py_DECREF(r);
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (m->per_instruction_opcodes == NULL || m->active_monitors.tools[g_instruction_event_index] != g_tool_bit ||
        m->active_monitors.tools[g_line_event_index] != 0) {
        PyMem_RawFree(before);
        revert_to_line_events(ci, (PyObject*)co);
        return -1;
    }
    /* CPython allocates per_instruction_tools only once two tools share an event, and empty, which
       would silence the probes armed then. Allocated now, CPython keeps it up to date. */
    if (m->per_instruction_tools == NULL) {
        uint8_t* tools = PyMem_Malloc((size_t)n);
        if (tools == NULL) {
            PyMem_RawFree(before);
            revert_to_line_events(ci, (PyObject*)co);
            return -1;
        }
        memset(tools, 0, (size_t)n);
        m->per_instruction_tools = tools;
    }
    int32_t k = 0;
    int missing = 0;
    for (Py_ssize_t i = co->_co_firsttraceable; i < n; i++) {
        int keep = k < n_positions && positions[k] == i;
        if (keep)
            k++;
        /* Skips what the switch left alone: RESUME, END_FOR and inline cache entries. */
        if (bytecode[2 * i] != g_instrumented_instruction || before[i] == g_instrumented_instruction) {
            missing |= keep;
            continue;
        }
        if (keep) {
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
    if (missing || k != n_positions) {
        revert_to_line_events(ci, (PyObject*)co);
        return -1;
    }
    return 0;
}

#if DD_HAVE_BLOCK_PROBES
static int
choose_block_probes(CodeInfo* ci, PyCodeObject* co);
#endif

/* Decides, at the code object's first LINE event, between LINE events, INSTRUCTION probes on the line
   starts and block probes. Never raises. */
static DD_COLD void
choose_probes(CodeInfo* ci, PyCodeObject* co)
{
    ci->probes = PROBES_LINE;
#if DD_HAVE_BLOCK_PROBES
    if (g_block_probes && !ci->is_init && choose_block_probes(ci, co))
        return;
#endif
    if (!g_instruction_probes || !can_switch_to_instruction_events(co) || !has_computed_lines(co))
        return;
    if (ci->starts == NULL && compute_starts(ci, co) < 0)
        return;
    if (compute_start_lines(ci, co) < 0)
        return;
    if (switch_to_instruction_events(ci, co, ci->starts, ci->n_starts) == 0) {
        ci->probes = PROBES_INSTRUCTION;
        g_switched++;
    }
}

/* Mirrors CPython's add_per_instruction_tools() for the given instructions. Returns 0 on success, 1
   when the code object's INSTRUCTION events are not this tool's alone any more. */
static int
rearm_positions(PyCodeObject* co, const int32_t* positions, int32_t n_positions)
{
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (m == NULL || m->lines == NULL || m->per_instruction_opcodes == NULL || m->per_instruction_tools == NULL ||
        !(m->active_monitors.tools[g_instruction_event_index] & g_tool_bit))
        return 1;
    uint8_t* bytecode = (uint8_t*)co->co_code_adaptive;
    for (int32_t k = 0; k < n_positions; k++) {
        int32_t i = positions[k];
        m->per_instruction_tools[i] |= g_tool_bit;
        uint8_t* op = &bytecode[2 * i];
        if (*op == g_instrumented_line)
            op = &m->lines[i].original_opcode; /* another tool's LINE event comes first */
        if (*op == g_instrumented_instruction)
            continue;
        m->per_instruction_opcodes[i] = *op >= g_min_instrumented ? *op : g_deopt[*op];
        *op = (uint8_t)g_instrumented_instruction;
    }
    return 0;
}

/* Re-arms the INSTRUCTION probes on every line start. Returns 0 on success, 1 when the code object
   must go back to LINE events. */
static int
rearm_instruction_probes(CodeInfo* ci, PyCodeObject* co)
{
    if (rearm_positions(co, ci->starts, ci->n_starts) != 0)
        return 1;
    g_rearm_instruction++;
    g_rearm_starts += (unsigned long long)ci->n_starts;
    return 0;
}
#endif

#if DD_HAVE_BLOCK_PROBES
static void
leave_block_mode(CodeInfo* ci);
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
#if DD_HAVE_BLOCK_PROBES
        if (ci->probes == PROBES_BLOCK) {
            if (rearm_positions((PyCodeObject*)code, ci->blocks->probe_index, ci->blocks->n_probes) == 0) {
                g_rearm_block++;
                g_rearm_starts += (unsigned long long)ci->blocks->n_probes;
            } else {
                leave_block_mode(ci);
            }
            done = 1;
        }
#endif
#if DD_HAVE_INSTRUCTION_PROBES
        if (!done && ci->probes == PROBES_INSTRUCTION) {
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

/* The active context a value of the context variable names, or NULL. */
static inline Context*
owner_of(PyObject* value)
{
    return value != NULL && Py_IS_TYPE(value, &ContextType) && ((Context*)value)->active ? (Context*)value : NULL;
}

#if DD_HAVE_CACHED_OWNER
/* The value of the context variable in the thread's current context, borrowed, when the variable's
   cache holds it; NULL otherwise. The conditions are PyContextVar_Get()'s for using its cache. */
static inline PyObject*
cached_value(PyThreadState* ts)
{
    const dd_context_var* var = (const dd_context_var*)g_ctxvar;
    PyObject* value = var->var_cached;
    if (value != NULL && ts->context != NULL && var->var_cached_tsid == ts->id &&
        var->var_cached_tsver == ts->context_ver)
        return value;
    return NULL;
}
#endif

/* The active context the running thread or task carries, or NULL. */
static inline Context*
current_owner(void)
{
#if DD_HAVE_CACHED_OWNER
    if (DD_LIKELY(g_cached_owner)) {
        PyObject* cached = cached_value(DD_THREAD_STATE());
        if (DD_LIKELY(cached != NULL))
            return owner_of(cached);
    }
#endif
    PyObject* current = NULL;
    if (DD_UNLIKELY(PyContextVar_Get(g_ctxvar, NULL, &current) < 0)) {
        PyErr_Clear();
        g_errors++;
        return NULL;
    }
    if (current == NULL)
        return NULL;
    Context* owner = owner_of(current);
    /* Borrowed from here on: the context variable keeps the object alive, and no Python code runs
       before the callback returns. */
    Py_DECREF(current);
    return owner;
}

/* Credits a line of the code object and returns what the callback returns. */
static inline PyObject*
record_line(CodeInfo* ci, uint32_t line)
{
    if (DD_UNLIKELY(ci->is_init))
        return record_init_line(ci, line);
    int32_t file_id = ci->file_id;
    Context* owner = g_n_active > 0 ? current_owner() : NULL;
    if (DD_LIKELY(owner != NULL)) {
        if (DD_UNLIKELY(context_set(owner, file_id, line) < 0)) {
            g_errors++;
            Py_RETURN_NONE;
        }
        /* The only active context, which now has the line. */
        if (DD_LIKELY(g_n_active == 1))
            return disable_line(ci);
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
        if (c != owner && !c->suspended && !context_has(c, file_id, line)) {
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

#if DD_HAVE_BLOCK_PROBES
/* ------------------------------------------------------------------------------------------------
 * Block probes (CPython 3.12).
 *
 * The bytecode of a code object is cut into basic blocks. A probe at the last line start of a block
 * credits the line starts of the block and of every block that dominates it: reaching the probe means
 * those ran, whole, since the frame last started or resumed. A block needs no probe of its own when
 * every path from it reaches a probed block it dominates before it leaves the blocks it dominates,
 * returns, raises, yields or comes back to it. Exception handlers and the instruction after each
 * yield are entry points of their own, so no inference crosses a catch or a suspension.
 *
 * A raise cuts a block short where the newest traceback entry says, when that entry is the frame's.
 * The handler that catches it credits that much, with a probe after its PUSH_EXC_INFO, and so does
 * the PY_UNWIND callback when it leaves the frame. A newest entry of another frame means a bare raise
 * or a RERAISE ended a block, which its probe credited. An exception thrown into a suspended generator
 * lands on its YIELD_VALUE, after the probe of the block that yields.
 *
 * Inference holds while the context a frame runs for stays the same. When it changes (a switch on
 * the thread, or the window of the context a thread runs for opening or closing), each frame with
 * block probes on the threads concerned is credited as far as it ran to the context it ran for, and
 * its code object moves to line-start probes for good, once its frames on other threads are credited
 * too. A thread runs for the context its innermost frame runs in: a frame below a
 * contextvars.Context.run() for another context is credited to that one.
 *
 * Greenlets switch frames without a yield: block probes stop for good once greenlet is imported.
 * Initialiser code keeps line-start probes.
 */
enum
{
    BK_NORMAL = 0,
    BK_COND = 1,         /* POP_JUMP_IF_*: forward, relative */
    BK_FORWARD = 2,      /* JUMP_FORWARD */
    BK_BACKWARD = 3,     /* JUMP_BACKWARD, JUMP_BACKWARD_NO_INTERRUPT */
    BK_FOR_ITER = 4,     /* exhausted: past its END_FOR; after a generator's return: to its END_FOR */
    BK_SEND = 5,         /* returned: to its END_SEND */
    BK_EXIT = 6,         /* RETURN_VALUE, RETURN_CONST, RAISE_VARARGS */
    BK_RERAISE = 7,      /* RERAISE */
    BK_YIELD = 8,        /* YIELD_VALUE */
    BK_EXTENDED_ARG = 9, /* EXTENDED_ARG */
    BK_PUSH_EXC_INFO = 10,
    BK_RERAISE_AT = 11,  /* CLEANUP_THROW, END_ASYNC_FOR: fall through, or re-raise where they are */
    BK_HANDLER = 12,     /* what a handler without PUSH_EXC_INFO may run before its RERAISE */
    BK_UNSUPPORTED = 13, /* a jump the analysis does not know */
    BK_COUNT
};

#define DD_MAX_BLOCKS 4096
#define DD_MAX_REHANDLE 16 /* re-raising handlers followed to find the one that catches */

/* Code objects with block probes: a CodeInfo with blocks is listed. */
static CodeInfo** g_block_codes = NULL;
static Py_ssize_t g_n_block_codes = 0;
static Py_ssize_t g_cap_block_codes = 0;

static DD_COLD int
list_block_code(CodeInfo* ci)
{
    if (g_n_block_codes == g_cap_block_codes) {
        Py_ssize_t cap = g_cap_block_codes ? g_cap_block_codes * 2 : 256;
        CodeInfo** codes = PyMem_RawRealloc(g_block_codes, sizeof(CodeInfo*) * (size_t)cap);
        if (codes == NULL)
            return -1;
        g_block_codes = codes;
        g_cap_block_codes = cap;
    }
    ci->block_slot = g_n_block_codes;
    g_block_codes[g_n_block_codes++] = ci;
    return 0;
}

static void
drop_blocks(CodeInfo* ci)
{
    if (ci->blocks == NULL)
        return;
    CodeInfo* last = g_block_codes[--g_n_block_codes];
    g_block_codes[ci->block_slot] = last;
    last->block_slot = ci->block_slot;
    PyMem_RawFree(ci->blocks);
    ci->blocks = NULL;
}

/* The block holding a code unit. */
static inline int32_t
block_of(const BlockInfo* bi, int32_t index)
{
    int32_t lo = 1, hi = bi->n_blocks;
    while (lo < hi) {
        int32_t mid = lo + (hi - lo) / 2;
        if (bi->first[mid] <= index)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo - 1;
}

/* The exception table entry covering a code unit, or -1. */
static int32_t
find_range(const int32_t* start, const int32_t* end, int32_t n, int32_t index)
{
    int32_t lo = 0, hi = n;
    while (lo < hi) {
        int32_t mid = lo + (hi - lo) / 2;
        if (start[mid] <= index)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo > 0 && index < end[lo - 1] ? lo - 1 : -1;
}

static inline int32_t
find_probe(const BlockInfo* bi, int32_t index)
{
    int32_t lo = 0, hi = bi->n_probes;
    while (lo < hi) {
        int32_t mid = lo + (hi - lo) / 2;
        if (bi->probe_index[mid] < index)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < bi->n_probes && bi->probe_index[lo] == index ? lo : -1;
}

/* ------------------------------------------------------------------------------------------------
 * Block analysis.
 */
#define DD_UNIT_INSN 1   /* an instruction starts at this code unit: not an inline cache entry */
#define DD_UNIT_LEADER 2 /* a block starts here */
#define DD_UNIT_ROOT 4   /* an entry point: the first instruction, a handler, or the one after a yield */

#define DD_BLOCK_SEEN 1
#define DD_BLOCK_ROOT 2
#define DD_BLOCK_PROBED 4

typedef struct
{
    int32_t succ[3]; /* blocks, or the exit node */
    int32_t n_succ;
} Edges;

typedef struct
{
    int32_t index;
    int32_t block;
    uint8_t caught;
} ProbeEntry;

static inline void
add_edge(Edges* e, int32_t to)
{
    for (int32_t k = 0; k < e->n_succ; k++)
        if (e->succ[k] == to)
            return;
    e->succ[e->n_succ++] = to;
}

static int
compare_probes(const void* a, const void* b)
{
    int32_t x = ((const ProbeEntry*)a)->index, y = ((const ProbeEntry*)b)->index;
    return (x > y) - (x < y);
}

static inline int
is_instruction(const uint8_t* flags, int32_t n, int64_t index)
{
    return index >= 0 && index < n && (flags[index] & DD_UNIT_INSN);
}

/* Reads an exception table varint: 6 bits a byte, 0x40 for more. */
static const uint8_t*
read_varint(const uint8_t* p, const uint8_t* end, int32_t* value)
{
    if (p >= end)
        return NULL;
    uint32_t v = *p & 63;
    while (*p & 64) {
        if (++p >= end || v >= (1u << 24))
            return NULL;
        v = (v << 6) | (*p & 63);
    }
    *value = (int32_t)v;
    return p + 1;
}

/* Where a handler that does not start with PUSH_EXC_INFO re-raises: its CLEANUP_THROW or END_ASYNC_FOR,
   or the RERAISE after a run of BK_HANDLER instructions. -1 for any other shape. */
static int32_t
reraise_position(const uint8_t* code, int32_t n, int32_t index)
{
    for (int steps = 0; index < n && steps < 64; steps++) {
        uint8_t op = code[2 * index];
        int kind = g_block_kind[op];
        if (kind == BK_RERAISE || (kind == BK_RERAISE_AT && steps == 0))
            return index;
        if (kind != BK_HANDLER && kind != BK_EXTENDED_ARG)
            return -1;
        index += 1 + g_cache_entries[op];
    }
    return -1;
}

/* The handler that catches what is raised in exception table entry r, past the handlers that only
   re-raise: its PUSH_EXC_INFO, -1 when the exception leaves the frame, -2 when the analysis gives up. */
static int32_t
catching_handler(const uint8_t* code,
                 int32_t n,
                 const int32_t* start,
                 const int32_t* end,
                 const int32_t* target,
                 int32_t n_ranges,
                 int32_t r)
{
    for (int step = 0; step < DD_MAX_REHANDLE; step++) {
        int32_t t = target[r];
        if (g_block_kind[code[2 * t]] == BK_PUSH_EXC_INFO)
            return t;
        int32_t at = reraise_position(code, n, t);
        if (at < 0)
            return -2;
        r = find_range(start, end, n_ranges, at);
        if (r < 0)
            return -1;
    }
    return -2;
}

/* The blocks and probes of a code object, or NULL when its bytecode is outside what the analysis
   knows. ci->starts and ci->start_lines must be set. Never raises. */
static DD_COLD BlockInfo*
analyse_blocks(CodeInfo* ci, PyCodeObject* co)
{
    PyObject* bytes = PyCode_GetCode(co); /* base opcodes, whatever the instrumentation */
    if (bytes == NULL) {
        PyErr_Clear();
        return NULL;
    }
    const uint8_t* code = (const uint8_t*)PyBytes_AS_STRING(bytes);
    const int32_t n = (int32_t)(PyBytes_GET_SIZE(bytes) / 2);
    const uint8_t* table = (const uint8_t*)PyBytes_AS_STRING(co->co_exceptiontable);
    const uint8_t* table_end = table + PyBytes_GET_SIZE(co->co_exceptiontable);
    const int32_t max_ranges = (int32_t)(PyBytes_GET_SIZE(co->co_exceptiontable) / 4) + 1;
    const int32_t exit_node = DD_MAX_BLOCKS + 1; /* above every block and the entry */

    BlockInfo* bi = NULL;
    uint8_t* flags = NULL;      /* per code unit, DD_UNIT_* */
    int32_t* unit_block = NULL; /* per code unit: its block */
    int32_t* ranges = NULL;     /* start, end and target of each exception table entry */
    int32_t* first = NULL;      /* per block, then n */
    int32_t* yield_at = NULL;   /* per block */
    Edges* edges = NULL;        /* per block */
    uint8_t* bflags = NULL;     /* per block, DD_BLOCK_* */
    uint32_t* mark = NULL;      /* per block: the last search that visited it */
    int32_t* scratch = NULL;
    ProbeEntry* entries = NULL;
    int32_t n_ranges = 0, nb = 0, n_entries = 0, np = 0, n_probed = 0;
    int32_t *range_start, *range_end, *range_target;
    uint32_t ext;

    flags = PyMem_RawCalloc((size_t)n + 1, 1);
    unit_block = PyMem_RawMalloc(sizeof(int32_t) * ((size_t)n + 1));
    ranges = PyMem_RawMalloc(sizeof(int32_t) * 3 * (size_t)max_ranges);
    if (flags == NULL || unit_block == NULL || ranges == NULL || n == 0)
        goto done;
    range_start = ranges;
    range_end = ranges + max_ranges;
    range_target = ranges + 2 * max_ranges;

    for (int32_t i = 0; i < n; i += 1 + g_cache_entries[code[2 * i]])
        flags[i] |= DD_UNIT_INSN;

    /* Leaders: jump targets, the instruction after each jump, return, raise or yield, and handlers. */
    flags[0] |= DD_UNIT_LEADER | DD_UNIT_ROOT;
    ext = 0;
    for (int32_t i = 0, next; i < n; i = next) {
        uint8_t op = code[2 * i];
        uint32_t oparg = (ext << 8) | code[2 * i + 1];
        int kind = g_block_kind[op];
        next = i + 1 + g_cache_entries[op];
        if (kind == BK_EXTENDED_ARG) {
            ext = oparg;
            continue;
        }
        ext = 0;
        int64_t targets[2];
        int n_targets = 0;
        switch (kind) {
            case BK_COND:
            case BK_FORWARD:
            case BK_SEND:
                targets[n_targets++] = (int64_t)next + oparg;
                break;
            case BK_BACKWARD:
                targets[n_targets++] = (int64_t)next - oparg;
                break;
            case BK_FOR_ITER:
                targets[n_targets++] = (int64_t)next + oparg; /* END_FOR */
                targets[n_targets++] = (int64_t)next + oparg + 1;
                break;
            case BK_EXIT:
            case BK_RERAISE:
                break;
            case BK_YIELD:
                if (next < n)
                    flags[next] |= DD_UNIT_ROOT;
                break;
            case BK_UNSUPPORTED:
                goto done;
            default:
                continue;
        }
        if ((kind == BK_COND || kind == BK_FOR_ITER || kind == BK_SEND) && next >= n)
            goto done;
        for (int k = 0; k < n_targets; k++) {
            if (!is_instruction(flags, n, targets[k]))
                goto done;
            flags[targets[k]] |= DD_UNIT_LEADER;
        }
        if (next < n)
            flags[next] |= DD_UNIT_LEADER;
    }
    for (const uint8_t* p = table; p < table_end;) {
        int32_t start, size, target, depth;
        if (!(*p & 0x80) || (p = read_varint(p, table_end, &start)) == NULL ||
            (p = read_varint(p, table_end, &size)) == NULL || (p = read_varint(p, table_end, &target)) == NULL ||
            (p = read_varint(p, table_end, &depth)) == NULL)
            goto done;
        if (n_ranges == max_ranges || size <= 0 || (int64_t)start + size > n || !is_instruction(flags, n, start) ||
            !is_instruction(flags, n, target) || (n_ranges > 0 && start < range_end[n_ranges - 1]))
            goto done;
        range_start[n_ranges] = start;
        range_end[n_ranges] = start + size;
        range_target[n_ranges] = target;
        n_ranges++;
        flags[target] |= DD_UNIT_LEADER | DD_UNIT_ROOT;
    }

    /* Blocks and their edges. */
    for (int32_t i = 0; i < n; i++)
        nb += (flags[i] & DD_UNIT_LEADER) != 0;
    if (nb > DD_MAX_BLOCKS)
        goto done;
    first = PyMem_RawMalloc(sizeof(int32_t) * ((size_t)nb + 1));
    yield_at = PyMem_RawMalloc(sizeof(int32_t) * (size_t)nb);
    edges = PyMem_RawCalloc((size_t)nb, sizeof(Edges));
    bflags = PyMem_RawCalloc((size_t)nb, 1);
    mark = PyMem_RawCalloc((size_t)nb, sizeof(uint32_t));
    if (first == NULL || yield_at == NULL || edges == NULL || bflags == NULL || mark == NULL)
        goto done;
    for (int32_t i = 0, b = -1; i < n; i++) {
        if (flags[i] & DD_UNIT_LEADER)
            first[++b] = i;
        unit_block[i] = b;
    }
    first[nb] = n;
    for (int32_t b = 0; b < nb; b++) {
        yield_at[b] = -1;
        if (flags[first[b]] & DD_UNIT_ROOT)
            bflags[b] |= DD_BLOCK_ROOT;
    }
    ext = 0;
    for (int32_t i = 0, next; i < n; i = next) {
        uint8_t op = code[2 * i];
        uint32_t oparg = (ext << 8) | code[2 * i + 1];
        int kind = g_block_kind[op];
        next = i + 1 + g_cache_entries[op];
        if (kind == BK_EXTENDED_ARG) {
            if (next < n && (flags[next] & DD_UNIT_LEADER))
                goto done;
            ext = oparg;
            continue;
        }
        ext = 0;
        if (next < n && !(flags[next] & DD_UNIT_LEADER))
            continue; /* not the last instruction of its block */
        int32_t b = unit_block[i];
        Edges* e = &edges[b];
        int32_t fall = next < n ? unit_block[next] : exit_node;
        switch (kind) {
            case BK_COND:
                add_edge(e, unit_block[next + oparg]);
                add_edge(e, fall);
                break;
            case BK_FORWARD:
                add_edge(e, unit_block[next + oparg]);
                break;
            case BK_BACKWARD:
                add_edge(e, unit_block[next - oparg]);
                break;
            case BK_FOR_ITER:
                add_edge(e, fall);
                add_edge(e, unit_block[next + oparg]);
                add_edge(e, unit_block[next + oparg + 1]);
                break;
            case BK_SEND:
                add_edge(e, fall);
                add_edge(e, unit_block[next + oparg]);
                break;
            case BK_YIELD:
                yield_at[b] = i;
                add_edge(e, exit_node);
                break;
            case BK_EXIT:
            case BK_RERAISE:
                add_edge(e, exit_node);
                break;
            default:
                add_edge(e, fall);
                break;
        }
    }

    {
        /* Nodes are the blocks and the entry, nb, which leads to every entry point. */
        const size_t nn = (size_t)nb + 1;
        scratch = PyMem_RawMalloc(sizeof(int32_t) * (12 * nn + 8 * (size_t)nb + 8));
        if (scratch == NULL)
            goto done;
        int32_t* post = scratch;                    /* nn: postorder */
        int32_t* rpo_num = post + nn;               /* nn: position in reverse postorder */
        int32_t* idom = rpo_num + nn;               /* nn */
        int32_t* stack_node = idom + nn;            /* nn */
        int32_t* stack_next = stack_node + nn;      /* nn */
        int32_t* pred_lo = stack_next + nn;         /* nn + 1 */
        int32_t* preds = pred_lo + nn + 1;          /* 4 nb: up to three edges per block, and the entry's */
        int32_t* child_lo = preds + 4 * (size_t)nb; /* nn + 1 */
        int32_t* children = child_lo + nn + 1;      /* nb */
        int32_t* pre = children + nb;               /* nn: dominator tree, preorder clock */
        int32_t* post_clock = pre + nn;             /* nn: dominator tree, postorder clock */
        int32_t* visit = post_clock + nn;           /* nn: dominator tree, preorder */
        int32_t* start_lo = visit + nn;             /* nn */
        int32_t* search = start_lo + nn;            /* 3 nb + 3 */
        const int32_t entry = nb;
        int32_t n_post = 0, n_visit = 0, clock = 0, sp;

        /* Reverse postorder from the entry points. A block the entry points do not reach becomes one, in
           case it runs anyway. */
        for (int pass = 0; pass < 2; pass++) {
            for (int32_t r = 0; r < nb; r++) {
                if ((bflags[r] & DD_BLOCK_SEEN) || (pass == 0 && !(bflags[r] & DD_BLOCK_ROOT)))
                    continue;
                bflags[r] |= DD_BLOCK_SEEN | DD_BLOCK_ROOT;
                stack_node[0] = r;
                stack_next[0] = 0;
                sp = 1;
                while (sp > 0) {
                    int32_t v = stack_node[sp - 1];
                    if (stack_next[sp - 1] < edges[v].n_succ) {
                        int32_t s = edges[v].succ[stack_next[sp - 1]++];
                        if (s < nb && !(bflags[s] & DD_BLOCK_SEEN)) {
                            bflags[s] |= DD_BLOCK_SEEN;
                            stack_node[sp] = s;
                            stack_next[sp] = 0;
                            sp++;
                        }
                    } else {
                        post[n_post++] = v;
                        sp--;
                    }
                }
            }
        }
        post[n_post++] = entry;
        for (int32_t j = 0; j < n_post; j++)
            rpo_num[post[j]] = n_post - 1 - j;

        /* Predecessors. */
        memset(pred_lo, 0, sizeof(int32_t) * (nn + 1));
        for (int32_t v = 0; v < nb; v++) {
            for (int32_t k = 0; k < edges[v].n_succ; k++)
                if (edges[v].succ[k] < nb)
                    pred_lo[edges[v].succ[k] + 1]++;
            if (bflags[v] & DD_BLOCK_ROOT)
                pred_lo[v + 1]++;
        }
        for (int32_t v = 0; v < nb; v++)
            pred_lo[v + 1] += pred_lo[v];
        memcpy(stack_next, pred_lo, sizeof(int32_t) * (size_t)nb);
        for (int32_t v = 0; v < nb; v++) {
            for (int32_t k = 0; k < edges[v].n_succ; k++)
                if (edges[v].succ[k] < nb)
                    preds[stack_next[edges[v].succ[k]]++] = v;
            if (bflags[v] & DD_BLOCK_ROOT)
                preds[stack_next[v]++] = entry;
        }

        /* Immediate dominators (Cooper, Harvey and Kennedy). */
        for (size_t k = 0; k < nn; k++)
            idom[k] = -1;
        idom[entry] = entry;
        for (int changed = 1; changed;) {
            changed = 0;
            for (int32_t j = n_post - 2; j >= 0; j--) {
                int32_t b = post[j];
                int32_t d = -1;
                for (int32_t q = pred_lo[b]; q < pred_lo[b + 1]; q++) {
                    int32_t x = preds[q];
                    if (idom[x] == -1)
                        continue;
                    if (d == -1) {
                        d = x;
                        continue;
                    }
                    int32_t y = d;
                    while (x != y) {
                        while (rpo_num[x] > rpo_num[y])
                            x = idom[x];
                        while (rpo_num[y] > rpo_num[x])
                            y = idom[y];
                    }
                    d = x;
                }
                if (d != idom[b]) {
                    idom[b] = d;
                    changed = 1;
                }
            }
        }

        /* Dominator tree, numbered so that d dominates v when pre[d] <= pre[v] and post_clock[v] <=
           post_clock[d]. */
        memset(child_lo, 0, sizeof(int32_t) * (nn + 1));
        for (int32_t b = 0; b < nb; b++)
            child_lo[idom[b] + 1]++;
        for (size_t k = 0; k < nn; k++)
            child_lo[k + 1] += child_lo[k];
        memcpy(stack_next, child_lo, sizeof(int32_t) * nn);
        for (int32_t b = 0; b < nb; b++)
            children[stack_next[idom[b]]++] = b;
        pre[entry] = clock++;
        visit[n_visit++] = entry;
        stack_node[0] = entry;
        stack_next[0] = child_lo[entry];
        sp = 1;
        while (sp > 0) {
            int32_t v = stack_node[sp - 1];
            if (stack_next[sp - 1] < child_lo[v + 1]) {
                int32_t c = children[stack_next[sp - 1]++];
                pre[c] = clock++;
                visit[n_visit++] = c;
                stack_node[sp] = c;
                stack_next[sp] = child_lo[c];
                sp++;
            } else {
                post_clock[v] = clock++;
                sp--;
            }
        }

        for (int32_t b = 0, s = 0; b < nb; b++) {
            while (s < ci->n_starts && ci->starts[s] < first[b])
                s++;
            start_lo[b] = s;
        }
        start_lo[nb] = ci->n_starts;

        /* Probes, dominated blocks first. A block with line starts needs one when a path from it leaves
           the blocks it dominates, exits or comes back to it before it reaches a dominated block with line
           starts: such a block has its own probe, or every path from it reaches one it dominates. */
        uint32_t generation = 0;
        for (int32_t m = n_visit - 1; m >= 1; m--) {
            int32_t g = visit[m];
            if (start_lo[g] == start_lo[g + 1])
                continue;
            generation++;
            sp = 0;
            for (int32_t k = 0; k < edges[g].n_succ; k++)
                search[sp++] = edges[g].succ[k];
            int needs = 0;
            while (sp > 0) {
                int32_t v = search[--sp];
                if (v >= nb || v == g) {
                    needs = 1;
                    break;
                }
                if (mark[v] == generation)
                    continue;
                mark[v] = generation;
                if (!(pre[g] <= pre[v] && post_clock[v] <= post_clock[g])) {
                    needs = 1;
                    break;
                }
                if (start_lo[v] < start_lo[v + 1])
                    continue;
                for (int32_t k = 0; k < edges[v].n_succ; k++)
                    search[sp++] = edges[v].succ[k];
            }
            if (needs) {
                bflags[g] |= DD_BLOCK_PROBED;
                n_probed++;
            }
        }

        /* Probe list: a probe at the last line start of each probed block, and one after each
           PUSH_EXC_INFO. A block probe on a PUSH_EXC_INFO moves to the handler probe after it. */
        entries = PyMem_RawMalloc(sizeof(ProbeEntry) * ((size_t)n_probed + (size_t)n_ranges + 1));
        if (entries == NULL)
            goto done;
        for (int32_t b = 0; b < nb; b++) {
            if (!(bflags[b] & DD_BLOCK_PROBED))
                continue;
            int32_t at = ci->starts[start_lo[b + 1] - 1];
            if (g_block_kind[code[2 * at]] == BK_PUSH_EXC_INFO && at + 1 < first[b + 1])
                at++;
            entries[n_entries].index = at;
            entries[n_entries].block = b;
            entries[n_entries].caught = 0;
            n_entries++;
        }
        for (int32_t r = 0; r < n_ranges; r++) {
            int32_t h = range_target[r];
            if (g_block_kind[code[2 * h]] == BK_PUSH_EXC_INFO) {
                if (h + 1 >= n || (flags[h + 1] & DD_UNIT_LEADER))
                    goto done;
                entries[n_entries].index = h + 1;
                entries[n_entries].block = -1;
                entries[n_entries].caught = 1;
                n_entries++;
            } else if (reraise_position(code, n, h) < 0) {
                goto done;
            }
        }
        qsort(entries, (size_t)n_entries, sizeof(ProbeEntry), compare_probes);
        for (int32_t k = 0; k < n_entries; k++) {
            if (np > 0 && entries[np - 1].index == entries[k].index) {
                if (entries[k].block >= 0)
                    entries[np - 1].block = entries[k].block;
                entries[np - 1].caught |= entries[k].caught;
            } else {
                entries[np++] = entries[k];
            }
        }

        size_t size = sizeof(BlockInfo) + sizeof(uint64_t) * (size_t)nb + sizeof(int32_t) * (4 * (size_t)nb + 2) +
                      sizeof(int32_t) * (2 * (size_t)np + 3 * (size_t)n_ranges) + (size_t)np;
        bi = PyMem_RawCalloc(1, size);
        if (bi == NULL)
            goto done;
        char* cursor = (char*)(bi + 1);
        bi->token = (uint64_t*)cursor;
        cursor += sizeof(uint64_t) * (size_t)nb;
        bi->first = (int32_t*)cursor;
        cursor += sizeof(int32_t) * ((size_t)nb + 1);
        bi->idom = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)nb;
        bi->start_lo = (int32_t*)cursor;
        cursor += sizeof(int32_t) * ((size_t)nb + 1);
        bi->yield_at = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)nb;
        bi->probe_index = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)np;
        bi->probe_block = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)np;
        bi->range_start = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)n_ranges;
        bi->range_end = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)n_ranges;
        bi->range_catch = (int32_t*)cursor;
        cursor += sizeof(int32_t) * (size_t)n_ranges;
        bi->probe_caught = (uint8_t*)cursor;
        bi->n_code = n;
        bi->n_blocks = nb;
        bi->n_probes = np;
        bi->n_ranges = n_ranges;
        memcpy(bi->first, first, sizeof(int32_t) * ((size_t)nb + 1));
        memcpy(bi->start_lo, start_lo, sizeof(int32_t) * ((size_t)nb + 1));
        memcpy(bi->yield_at, yield_at, sizeof(int32_t) * (size_t)nb);
        for (int32_t b = 0; b < nb; b++)
            bi->idom[b] = idom[b] == entry ? -1 : idom[b];
        for (int32_t k = 0; k < np; k++) {
            bi->probe_index[k] = entries[k].index;
            bi->probe_block[k] = entries[k].block;
            bi->probe_caught[k] = entries[k].caught;
        }
        for (int32_t r = 0; r < n_ranges; r++) {
            bi->range_start[r] = range_start[r];
            bi->range_end[r] = range_end[r];
            bi->range_catch[r] = catching_handler(code, n, range_start, range_end, range_target, n_ranges, r);
            if (bi->range_catch[r] == -2) {
                PyMem_RawFree(bi);
                bi = NULL;
                goto done;
            }
        }
    }

done:
    PyMem_RawFree(flags);
    PyMem_RawFree(unit_block);
    PyMem_RawFree(ranges);
    PyMem_RawFree(first);
    PyMem_RawFree(yield_at);
    PyMem_RawFree(edges);
    PyMem_RawFree(bflags);
    PyMem_RawFree(mark);
    PyMem_RawFree(scratch);
    PyMem_RawFree(entries);
    Py_DECREF(bytes);
    return bi;
}

/* ------------------------------------------------------------------------------------------------
 * Block crediting.
 */
static inline void
credit_line(CodeInfo* ci, Context* owner, int32_t line)
{
    if (line <= 0)
        return;
    int rc = owner != NULL ? context_set(owner, ci->file_id, (uint32_t)line)
                           : bitset_set(&g_files[ci->file_id].unowned_lines, (uint32_t)line);
    if (DD_UNLIKELY(rc < 0))
        g_errors++;
}

/* Credits block b and its dominators in full, up to the first one credited to owner already. */
static void
credit_chain(CodeInfo* ci, BlockInfo* bi, int32_t b, Context* owner)
{
    uint64_t token = owner != NULL ? owner->token : DD_TOKEN_UNOWNED;
    for (; b >= 0 && bi->token[b] != token; b = bi->idom[b]) {
        for (int32_t s = bi->start_lo[b]; s < bi->start_lo[b + 1]; s++)
            credit_line(ci, owner, ci->start_lines[s]);
        bi->token[b] = token;
    }
}

/* Credits block b up to code unit p, and its dominators in full. */
static void
credit_partial(CodeInfo* ci, BlockInfo* bi, int32_t b, int32_t p, Context* owner)
{
    for (int32_t s = bi->start_lo[b]; s < bi->start_lo[b + 1] && ci->starts[s] <= p; s++)
        credit_line(ci, owner, ci->start_lines[s]);
    credit_chain(ci, bi, bi->idom[b], owner);
}

/* Whether context c lacks a line that a probe of block b credits. */
static int
context_lacks_chain(CodeInfo* ci, BlockInfo* bi, int32_t b, Context* c)
{
    for (; b >= 0 && bi->token[b] != c->token; b = bi->idom[b]) {
        for (int32_t s = bi->start_lo[b]; s < bi->start_lo[b + 1]; s++) {
            int32_t line = ci->start_lines[s];
            if (line > 0 && !context_has(c, ci->file_id, (uint32_t)line))
                return 1;
        }
    }
    return 0;
}

/* The code unit an exception was raised at in the frame, from its newest traceback entry, or -1 when
   that entry is another frame's. */
static int32_t
raised_at(PyObject* exc, _PyInterpreterFrame* frame)
{
    if (exc == NULL || !PyExceptionInstance_Check(exc))
        return -1;
    PyObject* tb = ((PyBaseExceptionObject*)exc)->traceback;
    if (tb == NULL || !PyTraceBack_Check(tb))
        return -1;
    PyTracebackObject* entry = (PyTracebackObject*)tb;
    if (entry->tb_frame == NULL || entry->tb_frame->f_frame != frame || entry->tb_lasti < 0)
        return -1;
    return entry->tb_lasti / (int)sizeof(_Py_CODEUNIT);
}

static PyObject*
block_event(CodeInfo* ci, int32_t index)
{
    BlockInfo* bi = ci->blocks;
    int32_t k = find_probe(bi, index);
    /* Not a probe: sys.monitoring re-instrumented every instruction. */
    if (DD_UNLIKELY(k < 0))
        return Py_NewRef(g_disable);
    g_events_block++;
    Context* owner = g_n_active > 0 ? current_owner() : NULL;
    int32_t b = bi->probe_block[k];
    if (bi->probe_caught[k]) {
        /* Stays armed: each catch credits the block its raise cut short. */
        g_events_caught++;
        PyThreadState* ts = PyThreadState_Get();
        PyObject* exc = ts->exc_info != NULL ? ts->exc_info->exc_value : NULL;
        int32_t p = raised_at(exc, ts->cframe->current_frame);
        if (p >= 0 && p < bi->n_code) {
            int32_t at = block_of(bi, p);
            int32_t r = find_range(bi->range_start, bi->range_end, bi->n_ranges, p);
            if (bi->yield_at[at] != p && r >= 0 && bi->range_catch[r] == index - 1)
                credit_partial(ci, bi, at, p, owner);
        }
        if (b >= 0)
            credit_chain(ci, bi, b, owner);
        Py_RETURN_NONE;
    }
    credit_chain(ci, bi, b, owner);
    if (g_n_active == 0 || (g_n_active == 1 && g_active[0] == owner))
        return disable_line(ci);
    /* Keep the probe armed while an active context lacks a line it credits: it may run there next. */
    for (int j = 0; j < g_n_active; j++) {
        Context* c = g_active[j];
        if (c != owner && !c->suspended && context_lacks_chain(ci, bi, b, c)) {
            g_events_kept++;
            Py_RETURN_NONE;
        }
    }
    return disable_line(ci);
}

/* The PY_UNWIND callback: an exception leaves a frame. Returns None always, as PY_UNWIND events cannot
   be disabled. */
static PyObject*
unwind_event(PyObject* Py_UNUSED(callable), PyObject* const* args, size_t nargsf, PyObject* kwnames)
{
    if (DD_UNLIKELY(PyVectorcall_NARGS(nargsf) != 3 || kwnames != NULL))
        Py_RETURN_NONE;
    CodeInfo* ci = codeinfo_get(args[0]);
    if (DD_LIKELY(ci == NULL || ci->probes != PROBES_BLOCK))
        Py_RETURN_NONE;
    long offset = event_number(args[1]);
    if (DD_UNLIKELY(offset < 0))
        Py_RETURN_NONE;
    g_events_unwind++;
    BlockInfo* bi = ci->blocks;
    int32_t p = raised_at(args[2], PyThreadState_Get()->cframe->current_frame);
    if (p < 0)
        p = (int32_t)(offset / 2);
    if (p >= bi->n_code)
        Py_RETURN_NONE;
    int32_t b = block_of(bi, p);
    if (bi->yield_at[b] != p)
        credit_partial(ci, bi, b, p, g_n_active > 0 ? current_owner() : NULL);
    Py_RETURN_NONE;
}

/* ------------------------------------------------------------------------------------------------
 * Leaving block probes.
 */
static DD_COLD void
convert_to_starts(CodeInfo* ci)
{
    drop_blocks(ci);
    g_converted++;
    ci->probes = PROBES_INSTRUCTION;
    if (rearm_instruction_probes(ci, (PyCodeObject*)ci->code) != 0)
        revert_to_line_events(ci, ci->code);
}

/* Code objects whose frames a settle credits before they move to line-start probes. The list holds a
   reference to each code object. */
static CodeInfo** g_settle = NULL;
static Py_ssize_t g_n_settle = 0;
static Py_ssize_t g_cap_settle = 0;
static int g_settling = 0;

static void
list_for_settle(CodeInfo* ci)
{
    if (ci->settling)
        return;
    if (g_n_settle == g_cap_settle) {
        Py_ssize_t cap = g_cap_settle ? g_cap_settle * 2 : 64;
        CodeInfo** list = PyMem_RawRealloc(g_settle, sizeof(CodeInfo*) * (size_t)cap);
        if (list == NULL) {
            g_errors++;
            return;
        }
        g_settle = list;
        g_cap_settle = cap;
    }
    Py_INCREF(ci->code);
    g_settle[g_n_settle++] = ci;
    ci->settling = 1;
}

/* The CodeInfo of the code a frame runs when it has block probes, else NULL. */
static inline CodeInfo*
block_frame(_PyInterpreterFrame* f)
{
    if (f->owner == FRAME_OWNED_BY_CSTACK || _PyFrame_IsIncomplete(f))
        return NULL;
    CodeInfo* ci = codeinfo_get((PyObject*)f->f_code);
    return ci != NULL && ci->probes == PROBES_BLOCK ? ci : NULL;
}

static inline _PyInterpreterFrame*
innermost_frame(PyThreadState* ts)
{
    return ts->cframe != NULL ? ts->cframe->current_frame : NULL;
}

/* Credits what a frame ran: its block up to where it is, and that block's dominators. */
static void
credit_frame(CodeInfo* ci, _PyInterpreterFrame* f, Context* owner)
{
    BlockInfo* bi = ci->blocks;
    int32_t p = _PyInterpreterFrame_LASTI(f);
    if (p < 0 || p >= bi->n_code)
        return;
    int32_t b = block_of(bi, p);
    if (bi->yield_at[b] != p)
        credit_partial(ci, bi, b, p, owner);
}

/* The value of the context variable in the context a thread runs in, as a new reference, or NULL. */
static PyObject*
thread_value(PyThreadState* ts)
{
    PyObject* value = NULL;
    if (ts == PyThreadState_Get()) {
        if (PyContextVar_Get(g_ctxvar, NULL, &value) < 0) {
            PyErr_Clear();
            return NULL;
        }
        return value;
    }
    if (ts->context == NULL)
        return NULL;
    value = PyObject_GetItem(ts->context, g_ctxvar);
    if (value == NULL)
        PyErr_Clear();
    return value;
}

static int
has_block_frames(PyThreadState* ts)
{
    for (_PyInterpreterFrame* f = innermost_frame(ts); f != NULL; f = f->previous)
        if (block_frame(f) != NULL)
            return 1;
    return 0;
}

/* Credits each frame with block probes on the thread to owner, and lists its code object. */
static int
settle_thread(PyThreadState* ts, Context* owner)
{
    int found = 0;
    for (_PyInterpreterFrame* f = innermost_frame(ts); f != NULL; f = f->previous) {
        CodeInfo* ci = block_frame(f);
        if (ci != NULL) {
            credit_frame(ci, f, owner);
            list_for_settle(ci);
            found = 1;
        }
    }
    return found;
}

/* Credits the frames of the listed code objects on the other threads, each to the context its thread
   runs for, then moves the listed code objects to line-start probes. The threads settle_thread()
   credited already are skip_thread, or those that run for skip_carrying. */
static void
settle_listed(PyThreadState* skip_thread, Context* skip_carrying)
{
    if (g_n_settle == 0)
        return;
    PyThreadState* current = PyThreadState_Get();
    for (PyThreadState* ts = PyInterpreterState_ThreadHead(current->interp); ts != NULL; ts = PyThreadState_Next(ts)) {
        if (ts == skip_thread)
            continue;
        int known = 0;
        Context* owner = NULL;
        for (_PyInterpreterFrame* f = innermost_frame(ts); f != NULL; f = f->previous) {
            CodeInfo* ci = block_frame(f);
            if (ci == NULL || !ci->settling)
                continue;
            if (!known) {
                PyObject* value = thread_value(ts);
                int credited = skip_carrying != NULL && value == (PyObject*)skip_carrying;
                /* Borrowed past the decref: the thread's context holds the value. */
                owner = owner_of(value);
                Py_XDECREF(value);
                if (credited)
                    break;
                known = 1;
            }
            credit_frame(ci, f, owner);
        }
    }
    /* Taken, not cleared, as in rearm_all(). */
    CodeInfo** list = g_settle;
    Py_ssize_t n = g_n_settle, cap = g_cap_settle;
    g_settle = NULL;
    g_n_settle = 0;
    g_cap_settle = 0;
    for (Py_ssize_t k = 0; k < n; k++) {
        list[k]->settling = 0;
        if (list[k]->probes == PROBES_BLOCK)
            convert_to_starts(list[k]);
    }
    for (Py_ssize_t k = 0; k < n; k++)
        Py_DECREF(list[k]->code);
    if (g_settle == NULL) {
        g_settle = list;
        g_cap_settle = cap;
    } else {
        PyMem_RawFree(list);
    }
}

/* Before this thread switches from running for old_owner to running for another context. */
static void
settle_switch(Context* old_owner)
{
    if (DD_LIKELY(g_n_block_codes == 0) || g_settling)
        return;
    PyObject* raised = PyErr_GetRaisedException();
    g_settling = 1;
    PyThreadState* ts = PyThreadState_Get();
    if (settle_thread(ts, old_owner))
        settle_listed(ts, NULL);
    g_settling = 0;
    PyErr_SetRaisedException(raised);
}

/* Before the window of c opens or closes, which changes what the threads that carry c run for. */
static void
settle_window(Context* c)
{
    if (DD_LIKELY(g_n_block_codes == 0) || g_settling)
        return;
    PyObject* raised = PyErr_GetRaisedException();
    g_settling = 1;
    Context* owner = c->active ? c : NULL;
    int found = 0;
    PyThreadState* current = PyThreadState_Get();
    for (PyThreadState* ts = PyInterpreterState_ThreadHead(current->interp); ts != NULL; ts = PyThreadState_Next(ts)) {
        if (!has_block_frames(ts))
            continue;
        PyObject* value = thread_value(ts);
        Py_XDECREF(value);
        if (value == (PyObject*)c && settle_thread(ts, owner))
            found = 1;
    }
    if (found)
        settle_listed(NULL, c);
    g_settling = 0;
    PyErr_SetRaisedException(raised);
}

/* Moves code objects to line-start probes after crediting their frames: ci alone, or, with NULL, every
   code object with block probes. */
static void
settle_codes(CodeInfo* ci)
{
    if (g_settling) {
        if (ci != NULL)
            convert_to_starts(ci);
        return;
    }
    PyObject* raised = PyErr_GetRaisedException();
    g_settling = 1;
    if (ci != NULL) {
        list_for_settle(ci);
    } else {
        for (Py_ssize_t k = 0; k < g_n_block_codes; k++)
            list_for_settle(g_block_codes[k]);
    }
    settle_listed(NULL, NULL);
    g_settling = 0;
    PyErr_SetRaisedException(raised);
}

/* When a re-arm finds the code object's INSTRUCTION events gone. */
static void
leave_block_mode(CodeInfo* ci)
{
    settle_codes(ci);
}

/* Moves a code object to block probes at its first LINE event. Returns 1 when that decides its probes,
   0 when line-start probes are worth a try. */
static DD_COLD int
choose_block_probes(CodeInfo* ci, PyCodeObject* co)
{
    if (!can_switch_to_instruction_events(co))
        return 0;
    if (ci->starts == NULL && compute_starts(ci, co) < 0)
        return 0;
    if (compute_start_lines(ci, co) < 0)
        return 0;
    BlockInfo* bi = analyse_blocks(ci, co);
    if (bi == NULL) {
        g_block_refused++;
        return 0;
    }
    if (list_block_code(ci) < 0) {
        PyMem_RawFree(bi);
        return 0;
    }
    ci->blocks = bi;
    if (switch_to_instruction_events(ci, co, bi->probe_index, bi->n_probes) != 0) {
        drop_blocks(ci); /* back on LINE events */
        return 1;
    }
    ci->probes = PROBES_BLOCK;
    g_switched_block++;
    return 1;
}
#endif

#if DD_HAVE_INSTRUCTION_PROBES
static PyObject*
instruction_event(PyObject* Py_UNUSED(callable), PyObject* const* args, size_t nargsf, PyObject* kwnames)
{
    if (DD_UNLIKELY(PyVectorcall_NARGS(nargsf) != 2 || kwnames != NULL))
        return Py_NewRef(g_disable);
    CodeInfo* ci = codeinfo_get(args[0]);
    if (DD_UNLIKELY(ci == NULL))
        return Py_NewRef(g_disable);
    long offset = event_number(args[1]);
    if (DD_UNLIKELY(offset < 0))
        return Py_NewRef(g_disable);
    int32_t index = (int32_t)(offset / 2);
#if DD_HAVE_BLOCK_PROBES
    if (ci->probes == PROBES_BLOCK)
        return block_event(ci, index);
#endif
    if (DD_UNLIKELY(ci->probes != PROBES_INSTRUCTION))
        return Py_NewRef(g_disable);
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
    c->token = g_next_token++;
    return (PyObject*)c;
}

/* Tells child processes started from now on which context they run for. */
static inline void
publish_owner(PyObject* running)
{
#if DD_HAVE_SHARED_STATE
    if (g_state == NULL)
        return;
    uint64_t token = running != NULL && Py_IS_TYPE(running, &ContextType) ? ((Context*)running)->token : 0;
    __atomic_store_n(&g_state->owner, token, __ATOMIC_RELAXED);
#else
    (void)running;
#endif
}

/* Ends the suspension c's window put on the context it was opened over. With rearm, the lines
   disabled while it was suspended run there again. */
static void
resume_outer(Context* c, int rearm)
{
    Context* outer = c->outer;
    if (outer == NULL)
        return;
    c->outer = NULL;
    outer->suspended--;
    if (rearm && outer->active && outer->suspended == 0)
        rearm_all();
    Py_DECREF(outer);
}

/* Closes the window of c. settle is 0 only when nothing can carry c any more. */
static void
context_deactivate(Context* c, int settle)
{
    if (!c->active)
        return;
#if DD_HAVE_BLOCK_PROBES
    if (settle)
        settle_window(c);
#endif
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
    resume_outer(c, settle);
}

static int
Context_traverse(Context* c, visitproc visit, void* arg)
{
    Py_VISIT(c->fixtures);
    Py_VISIT(c->outer);
    Py_VISIT(c->previous);
    return 0;
}

static int
Context_clear(Context* c)
{
    resume_outer(c, 0);
    Py_CLEAR(c->fixtures);
    Py_CLEAR(c->previous);
    return 0;
}

static void
Context_dealloc(Context* c)
{
    PyObject_GC_UnTrack(c);
    /* No context variable holds c, so no thread runs for it. */
    context_deactivate(c, 0);
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
#if DD_HAVE_BLOCK_PROBES
    settle_window(c);
#endif
    rearm_all();
    c->index = g_n_active;
    g_active[g_n_active++] = c;
    c->active = 1;
    Py_RETURN_NONE;
}

static PyObject*
Context_pop(Context* c, PyObject* Py_UNUSED(ignored))
{
    context_deactivate(c, 1);
    Py_RETURN_NONE;
}

/* enter() -> previous: push, then make this context the running one (the ctxvar init was given).
   Returns the running context it replaced, for exit(). An active context it replaced is suspended
   until this one's window closes. */
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
#if DD_HAVE_BLOCK_PROBES
    Context* old_owner = owner_of(previous);
    if (old_owner != c)
        settle_switch(old_owner);
#endif
    PyObject* r = Context_push(c, NULL);
    if (r == NULL) {
        Py_DECREF(previous);
        return NULL;
    }
    Py_DECREF(r);
    PyObject* token = PyContextVar_Set(g_ctxvar, (PyObject*)c);
    if (token == NULL) {
        context_deactivate(c, 1);
        Py_DECREF(previous);
        return NULL;
    }
    Py_DECREF(token);
    Context* outer = owner_of(previous);
    if (outer != NULL && outer != c && c->outer == NULL) {
        c->outer = (Context*)Py_NewRef(outer);
        outer->suspended++;
    }
    publish_owner((PyObject*)c);
    return previous;
}

/* exit(previous): make previous the running context again, then pop. */
static PyObject*
Context_exit(Context* c, PyObject* previous)
{
#if DD_HAVE_BLOCK_PROBES
    Context* old_owner = g_n_active > 0 ? current_owner() : NULL;
    Context* new_owner = previous == (PyObject*)c ? NULL : owner_of(previous);
    if (old_owner != new_owner)
        settle_switch(old_owner);
#endif
    PyObject* token = PyContextVar_Set(g_ctxvar, previous);
    context_deactivate(c, 1);
    if (token == NULL)
        return NULL;
    Py_DECREF(token);
    publish_owner(previous);
    Py_RETURN_NONE;
}

/* Calls the records callback when child processes wrote records since it last ran. */
static int
collect_records(void)
{
#if DD_HAVE_SHARED_STATE
    if (g_records_callback == NULL || g_state == NULL)
        return 0;
    uint64_t records = __atomic_load_n(&g_state->records, __ATOMIC_ACQUIRE);
    if (records == g_records_seen)
        return 0;
    g_records_seen = records;
    /* A strong reference: the callback can replace itself. */
    PyObject* callback = Py_NewRef(g_records_callback);
    PyObject* r = PyObject_CallNoArgs(callback);
    Py_DECREF(callback);
    if (r == NULL)
        return -1;
    Py_DECREF(r);
#endif
    return 0;
}

/* __enter__() -> self: enter(), keeping the running context it replaced for __exit__. A test window
   is opened and closed this way, with no Python frame. */
static PyObject*
Context_enter_window(Context* c, PyObject* Py_UNUSED(ignored))
{
    if (c->previous != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "the context is entered already");
        return NULL;
    }
    PyObject* previous = Context_enter(c, NULL);
    if (previous == NULL)
        return NULL;
    c->previous = previous;
    return Py_NewRef(c);
}

/* __exit__(*exc_info): merge the records of the child processes that exited, which ran inside the
   window, then exit() back to the context __enter__ replaced. Never suppresses an exception. */
static PyObject*
Context_exit_window(Context* c, PyObject* const* Py_UNUSED(args), Py_ssize_t Py_UNUSED(nargs))
{
    PyObject* previous = c->previous;
    if (previous == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "the context is not entered");
        return NULL;
    }
    /* The window closes even when the callback fails, and the callback's error is reported after. */
    int collected = collect_records();
    PyObject* error = collected < 0 ? PyErr_GetRaisedException() : NULL;
    c->previous = NULL;
    PyObject* r = Context_exit(c, previous);
    Py_DECREF(previous);
    if (error != NULL) {
        Py_XDECREF(r);
        PyErr_SetRaisedException(error);
        return NULL;
    }
    return r;
}

/* include(enclosing): add the lines of enclosing, the window of the same test opened around this one,
   to this context's record. Only enclosing's own lines: the suite fixtures it linked when it opened
   may have been replaced since, by a setup for another parameter, and this record links the current
   ones itself. Anything but another Context is ignored. */
static PyObject*
Context_include(Context* c, PyObject* enclosing)
{
    if (!Py_IS_TYPE(enclosing, &ContextType) || enclosing == (PyObject*)c)
        Py_RETURN_NONE;
    PyObject* linked = ((Context*)enclosing)->fixtures;
    if (linked != NULL && PyList_SetSlice(linked, 0, PyList_GET_SIZE(linked), NULL) < 0)
        return NULL;
    if (c->fixtures == NULL && (c->fixtures = PyList_New(0)) == NULL)
        return NULL;
    if (PyList_Append(c->fixtures, enclosing) < 0)
        return NULL;
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

/* The files a record covers: the context's, those of the fixture contexts it depends on, each once,
   in first-touch order. Files outside the workspace are left out. */
typedef struct
{
    Context** ctxs;
    Py_ssize_t n_ctxs;
    int32_t* files;
    uint32_t* sizes; /* merged bitmap size of each file, in bytes */
    Py_ssize_t n_files;
    int include_init;
} Record;

static void
record_free(Record* r)
{
    PyMem_RawFree(r->ctxs);
    PyMem_RawFree(r->files);
    PyMem_RawFree(r->sizes);
}

static int
record_collect(Record* r, Context* c, int include_init)
{
    memset(r, 0, sizeof(*r));
    r->include_init = include_init;
    Py_ssize_t cap_ctxs = 0;
    if (collect_contexts(c, &r->ctxs, &r->n_ctxs, &cap_ctxs) < 0)
        return -1;
    Py_ssize_t touched = 0;
    for (Py_ssize_t j = 0; j < r->n_ctxs; j++)
        touched += r->ctxs[j]->n_touched;
    size_t count = touched > 0 ? (size_t)touched : 1;
    uint8_t* seen = PyMem_RawCalloc(g_n_files > 0 ? (size_t)g_n_files : 1, 1);
    r->files = PyMem_RawMalloc(sizeof(int32_t) * count);
    r->sizes = PyMem_RawMalloc(sizeof(uint32_t) * count);
    if (seen == NULL || r->files == NULL || r->sizes == NULL) {
        PyMem_RawFree(seen);
        PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t j = 0; j < r->n_ctxs; j++) {
        Context* source = r->ctxs[j];
        for (int32_t k = 0; k < source->n_touched; k++) {
            int32_t f = source->touched[k];
            if (seen[f])
                continue;
            seen[f] = 1;
            FileInfo* fi = &g_files[f];
            if (fi->report_path == Py_None)
                continue;
            uint32_t n = include_init ? bitset_used(&fi->init_lines) : 0;
            for (Py_ssize_t i = 0; i < r->n_ctxs; i++) {
                if (f < r->ctxs[i]->cap_files) {
                    uint32_t used = bitset_used(&r->ctxs[i]->files[f]);
                    if (used > n)
                        n = used;
                }
            }
            r->files[r->n_files] = f;
            r->sizes[r->n_files] = n;
            r->n_files++;
        }
    }
    PyMem_RawFree(seen);
    return 0;
}

/* Writes the merged bitmap of the record's k-th file to out, which holds its size. */
static void
record_merge(const Record* r, Py_ssize_t k, uint8_t* out)
{
    int32_t f = r->files[k];
    memset(out, 0, r->sizes[k]);
    if (r->include_init) {
        const Bitset* init = &g_files[f].init_lines;
        for (uint32_t b = 0; b < init->used; b++)
            out[b] |= init->bits[b];
    }
    for (Py_ssize_t i = 0; i < r->n_ctxs; i++) {
        if (f >= r->ctxs[i]->cap_files)
            continue;
        const Bitset* bits = &r->ctxs[i]->files[f];
        for (uint32_t b = 0; b < bits->used; b++)
            out[b] |= bits->bits[b];
    }
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
    Record r;
    if (record_collect(&r, c, include_init) < 0) {
        record_free(&r);
        return NULL;
    }
    PyObject* result = PyList_New(r.n_files);
    for (Py_ssize_t k = 0; result != NULL && k < r.n_files; k++) {
        PyObject* bytes = PyBytes_FromStringAndSize(NULL, r.sizes[k]);
        if (bytes == NULL) {
            Py_CLEAR(result);
            break;
        }
        record_merge(&r, k, (uint8_t*)PyBytes_AS_STRING(bytes));
        PyObject* item = PyTuple_Pack(2, g_files[r.files[k]].report_path, bytes);
        Py_DECREF(bytes);
        if (item == NULL) {
            Py_CLEAR(result);
            break;
        }
        PyList_SET_ITEM(result, k, item);
    }
    record_free(&r);
    return result;
}

/* msgpack headers, the shortest form for each length, as the tracer's own packer writes them. */
static inline size_t
msgpack_str_header_size(size_t n)
{
    return n <= 31 ? 1 : n <= 0xff ? 2 : n <= 0xffff ? 3 : 5;
}

static inline size_t
msgpack_bin_header_size(size_t n)
{
    return n <= 0xff ? 2 : n <= 0xffff ? 3 : 5;
}

static inline size_t
msgpack_array_header_size(size_t n)
{
    return n <= 15 ? 1 : n <= 0xffff ? 3 : 5;
}

static inline uint8_t*
msgpack_put_length(uint8_t* p, uint8_t tag8, uint8_t tag16, uint8_t tag32, size_t n)
{
    if (n <= 0xff && tag8 != 0) {
        *p++ = tag8;
        *p++ = (uint8_t)n;
    } else if (n <= 0xffff) {
        *p++ = tag16;
        *p++ = (uint8_t)(n >> 8);
        *p++ = (uint8_t)n;
    } else {
        *p++ = tag32;
        *p++ = (uint8_t)(n >> 24);
        *p++ = (uint8_t)(n >> 16);
        *p++ = (uint8_t)(n >> 8);
        *p++ = (uint8_t)n;
    }
    return p;
}

static inline uint8_t*
msgpack_put_str(uint8_t* p, const char* s, size_t n)
{
    if (n <= 31)
        *p++ = (uint8_t)(0xa0 | n);
    else
        p = msgpack_put_length(p, 0xd9, 0xda, 0xdb, n);
    memcpy(p, s, n);
    return p + n;
}

/* pack(path_key, bitmap_key) -> (count, bytes): the files bitmaps(True) returns, as a msgpack
   array of count maps {path_key: report path, bitmap_key: bitmap}. A record is packed per test,
   so this builds no Python object per file. */
static PyObject*
Context_pack(Context* c, PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 2 || !PyUnicode_Check(args[0]) || !PyUnicode_Check(args[1])) {
        PyErr_SetString(PyExc_TypeError, "pack() takes two str keys");
        return NULL;
    }
    Py_ssize_t path_key_size, bitmap_key_size;
    const char* path_key = PyUnicode_AsUTF8AndSize(args[0], &path_key_size);
    const char* bitmap_key = PyUnicode_AsUTF8AndSize(args[1], &bitmap_key_size);
    if (path_key == NULL || bitmap_key == NULL)
        return NULL;
    Record r;
    if (record_collect(&r, c, 1) < 0) {
        record_free(&r);
        return NULL;
    }
    PyObject* result = NULL;
    PyObject* bytes = NULL;
    uint8_t* p = NULL;
    size_t slots = (size_t)(r.n_files > 0 ? r.n_files : 1);
    const char** paths = PyMem_RawMalloc(sizeof(char*) * slots);
    Py_ssize_t* path_sizes = PyMem_RawMalloc(sizeof(Py_ssize_t) * slots);
    if (paths == NULL || path_sizes == NULL) {
        PyErr_NoMemory();
        goto done;
    }
    size_t total = msgpack_array_header_size((size_t)r.n_files);
    size_t entry = 1 + msgpack_str_header_size((size_t)path_key_size) + (size_t)path_key_size +
                   msgpack_str_header_size((size_t)bitmap_key_size) + (size_t)bitmap_key_size;
    for (Py_ssize_t k = 0; k < r.n_files; k++) {
        paths[k] = PyUnicode_AsUTF8AndSize(g_files[r.files[k]].report_path, &path_sizes[k]);
        if (paths[k] == NULL)
            goto done;
        total += entry + msgpack_str_header_size((size_t)path_sizes[k]) + (size_t)path_sizes[k] +
                 msgpack_bin_header_size(r.sizes[k]) + r.sizes[k];
    }
    bytes = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)total);
    if (bytes == NULL)
        goto done;
    p = (uint8_t*)PyBytes_AS_STRING(bytes);
    if (r.n_files <= 15)
        *p++ = (uint8_t)(0x90 | r.n_files);
    else
        p = msgpack_put_length(p, 0, 0xdc, 0xdd, (size_t)r.n_files);
    for (Py_ssize_t k = 0; k < r.n_files; k++) {
        *p++ = 0x82;
        p = msgpack_put_str(p, path_key, (size_t)path_key_size);
        p = msgpack_put_str(p, paths[k], (size_t)path_sizes[k]);
        p = msgpack_put_str(p, bitmap_key, (size_t)bitmap_key_size);
        p = msgpack_put_length(p, 0xc4, 0xc5, 0xc6, r.sizes[k]);
        record_merge(&r, k, p);
        p += r.sizes[k];
    }
    result = Py_BuildValue("(nN)", r.n_files, bytes);
done:
    PyMem_RawFree(paths);
    PyMem_RawFree(path_sizes);
    record_free(&r);
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

static PyObject*
Context_get_token(Context* c, void* Py_UNUSED(closure))
{
    return PyLong_FromUnsignedLongLong(c->token);
}

static PyMethodDef Context_methods[] = {
    { "push", (PyCFunction)Context_push, METH_NOARGS, "Activate, re-arming disabled lines." },
    { "pop", (PyCFunction)Context_pop, METH_NOARGS, "Deactivate." },
    { "enter",
      (PyCFunction)Context_enter,
      METH_NOARGS,
      "Push and run in this context, suspending the previous one until this one pops; returns the previous one." },
    { "exit", (PyCFunction)Context_exit, METH_O, "Run in the given previous context again, and pop." },
    { "__enter__", (PyCFunction)Context_enter_window, METH_NOARGS, "enter(), keeping the context it replaced." },
    { "__exit__",
      (PyCFunction)(void (*)(void))Context_exit_window,
      METH_FASTCALL,
      "Merge the records of child processes that exited, then exit() to the context __enter__ replaced." },
    { "include", (PyCFunction)Context_include, METH_O, "Add the own lines of the window around this one." },
    { "bitmaps", (PyCFunction)(void (*)(void))Context_bitmaps, METH_FASTCALL, "Record bitmaps." },
    { "pack", (PyCFunction)(void (*)(void))Context_pack, METH_FASTCALL, "Record bitmaps, packed as msgpack." },
    { "covered", (PyCFunction)Context_covered, METH_NOARGS, "Own lines by absolute path." },
    { NULL, NULL, 0, NULL },
};

static PyGetSetDef Context_getset[] = {
    { "active", (getter)Context_get_active, NULL, NULL, NULL },
    { "is_fixture", (getter)Context_get_is_fixture, NULL, NULL, NULL },
    { "fixtures",
      (getter)Context_get_fixtures,
      NULL,
      "Contexts whose lines this one's record includes: the fixture setups it depends on, or a window around it.",
      NULL },
    { "token", (getter)Context_get_token, NULL, "Names the context to child processes.", NULL },
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

/* Block probes need what INSTRUCTION probes need, and the opcode tables. */
static int
block_probes_possible(void)
{
    return DD_HAVE_BLOCK_PROBES && g_internal_rearm && g_instruction_event_index >= 0 &&
           g_instrumented_instruction > 0 && g_min_instrumented < 256 && g_block_tables;
}

#if DD_HAVE_CACHED_OWNER
/* Whether cached_value() agrees with PyContextVar_Get() on the thread now: it must hold the value
   PyContextVar_Get() returns when expect_hit is set, and nothing otherwise. Read before the call,
   which fills the cache when it finds a value. */
static int
cache_agrees(PyThreadState* ts, int expect_hit)
{
    PyObject* cached = cached_value(ts);
    PyObject* value = NULL;
    if (PyContextVar_Get(g_ctxvar, NULL, &value) < 0)
        return 0;
    int ok = expect_hit ? cached != NULL && cached == value : cached == NULL;
    Py_XDECREF(value);
    return ok;
}
#endif

/* Checks the mirrored context variable layout and the cache rules against PyContextVar_Get(), in two
   temporary contexts entered and left on this thread: the context the thread runs, and the values
   it holds, stay as they were. Returns 1 when the callbacks may read the cache. */
static int
cached_owner_self_test(void)
{
#if DD_HAVE_CACHED_OWNER
    if (!PyContextVar_CheckExact(g_ctxvar) || PyContextVar_Type.tp_basicsize != (Py_ssize_t)sizeof(dd_context_var))
        return 0;
    const dd_context_var* var = (const dd_context_var*)g_ctxvar;
    PyThreadState* ts = DD_THREAD_STATE();
    int ok = 0;
    int entered = 0; /* 1: in outer, 2: in inner */
    PyObject* token = NULL;
    PyObject* value = NULL;
    PyObject* name = PyObject_GetAttrString(g_ctxvar, "name");
    Py_hash_t hash = name != NULL ? PyObject_Hash(g_ctxvar) : -1;
    PyObject* outer = PyContext_New();
    PyObject* inner = PyContext_New();
    PyObject* probe = PyObject_CallNoArgs((PyObject*)&PyBaseObject_Type);
    if (ts == NULL || name == NULL || hash == -1 || outer == NULL || inner == NULL || probe == NULL)
        goto done;
    if (name != var->var_name || hash != var->var_hash)
        goto done;
    if (PyContext_Enter(outer) < 0)
        goto done;
    entered = 1;
    /* An empty context: the variable has its default, and the cache holds nothing for it. */
    if (!cache_agrees(ts, 0) || PyContextVar_Get(g_ctxvar, NULL, &value) < 0 || value != var->var_default)
        goto done;
    /* Setting the variable fills the cache. */
    if ((token = PyContextVar_Set(g_ctxvar, probe)) == NULL || !cache_agrees(ts, 1))
        goto done;
    /* Another context on the same thread: the cached value is not its value. */
    if (PyContext_Enter(inner) < 0)
        goto done;
    entered = 2;
    if (!cache_agrees(ts, 0))
        goto done;
    entered = 1;
    if (PyContext_Exit(inner) < 0)
        goto done;
    /* Back in the context that holds the value: the cache holds nothing until a lookup fills it. */
    ok = cache_agrees(ts, 0) && cache_agrees(ts, 1);
done:
    if (entered == 2 && PyContext_Exit(inner) < 0)
        ok = 0;
    if (entered >= 1) {
        if (token != NULL && PyContextVar_Reset(g_ctxvar, token) < 0)
            ok = 0;
        if (PyContext_Exit(outer) < 0)
            ok = 0;
    }
    if (PyErr_Occurred())
        PyErr_Clear();
    Py_XDECREF(value);
    Py_XDECREF(token);
    Py_XDECREF(probe);
    Py_XDECREF(inner);
    Py_XDECREF(outer);
    Py_XDECREF(name);
    return ok;
#else
    return 0;
#endif
}

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
                              "child",
                              "block_probes",
                              "block_kinds",
                              "cache_entries",
                              NULL };
    int tool_id, line_event_index, instrumented_line, enter_executor, instrumented_instruction;
    int internal_rearm = 1, instruction_event_index = -1, min_instrumented = 256, instruction_probes = 0;
    int child = 0, block_probes = 0;
    PyObject *disable, *set_local_events, *ctxvar, *superinstructions = NULL;
    PyObject *block_kinds = NULL, *cache_entries = NULL;
    Py_buffer deopt;
    if (!PyArg_ParseTupleAndKeywords(args,
                                     kwds,
                                     "iiOOOy*iii|piiOpppOO",
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
                                     &instruction_probes,
                                     &child,
                                     &block_probes,
                                     &block_kinds,
                                     &cache_entries))
        return NULL;
    if (g_child) {
        PyBuffer_Release(&deopt);
        PyErr_SetString(PyExc_RuntimeError, "a child process bootstrap already uses the collector");
        return NULL;
    }
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
    int have_tables =
      block_kinds != NULL && block_kinds != Py_None && cache_entries != NULL && cache_entries != Py_None;
    if (have_tables && (!PyBytes_Check(block_kinds) || PyBytes_GET_SIZE(block_kinds) != 256 ||
                        !PyBytes_Check(cache_entries) || PyBytes_GET_SIZE(cache_entries) != 256)) {
        PyBuffer_Release(&deopt);
        PyErr_SetString(PyExc_ValueError, "block_kinds and cache_entries must be 256 bytes");
        return NULL;
    }
    memcpy(g_deopt, deopt.buf, 256);
    PyBuffer_Release(&deopt);
    if (superinstructions != NULL && superinstructions != Py_None)
        memcpy(g_superinstruction, PyBytes_AS_STRING(superinstructions), 256);
    else
        memset(g_superinstruction, 0, 256);
    if (have_tables) {
        memcpy(g_block_kind, PyBytes_AS_STRING(block_kinds), 256);
        memcpy(g_cache_entries, PyBytes_AS_STRING(cache_entries), 256);
    }
    g_block_tables = have_tables;
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
    g_cached_owner = cached_owner_self_test();
    g_instrumented_line = instrumented_line;
    g_enter_executor = enter_executor;
    g_instrumented_instruction = instrumented_instruction;
    g_instruction_event_index = instruction_event_index;
    g_min_instrumented = min_instrumented;
    g_internal_rearm = DD_HAVE_INTERNAL_REARM && internal_rearm && instrumented_line > 0;
    g_instruction_probes = DD_HAVE_INSTRUCTION_PROBES && g_internal_rearm && instruction_probes &&
                           instruction_event_index >= 0 && instrumented_instruction > 0 && min_instrumented < 256;
    g_block_probes = block_probes_possible() && block_probes;
    g_initialized = 1;
    g_child = child;
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
    if (!g_internal_rearm) {
        g_instruction_probes = 0;
        g_block_probes = 0;
    }
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

/* Switches the callbacks between the context variable's cache and PyContextVar_Get(). Switching the
   cache on runs the self-test again, and stays off if it fails. */
static PyObject*
mod_set_cached_owner(PyObject* Py_UNUSED(self), PyObject* arg)
{
    int on = PyObject_IsTrue(arg);
    if (on < 0)
        return NULL;
    if (g_ctxvar == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "_linecov.init() was not called");
        return NULL;
    }
    g_cached_owner = on && cached_owner_self_test();
    return PyBool_FromLong(g_cached_owner);
}

/* Switches block probes for code objects not decided yet. */
static PyObject*
mod_set_block_probes(PyObject* Py_UNUSED(self), PyObject* arg)
{
    int on = PyObject_IsTrue(arg);
    if (on < 0)
        return NULL;
    g_block_probes = on && block_probes_possible();
    return PyBool_FromLong(g_block_probes);
}

/* Stops block probes for good: every code object with block probes moves to line-start probes, once
   what its frames ran is credited. */
static PyObject*
mod_disable_blocks(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    g_block_probes = 0;
#if DD_HAVE_BLOCK_PROBES
    if (g_n_block_codes > 0)
        settle_codes(NULL);
#endif
    Py_RETURN_NONE;
}

/* blocks(code) -> the block analysis of an attached code object, for tests, or None when the analysis
   refuses it: {"blocks": [(first, lines, idom, probed)], "probes": [(index, block, caught)],
   "ranges": [(start, end, catch)]}. */
static PyObject*
mod_blocks(PyObject* Py_UNUSED(self), PyObject* code)
{
    if (!PyCode_Check(code)) {
        PyErr_SetString(PyExc_TypeError, "blocks(code)");
        return NULL;
    }
#if DD_HAVE_BLOCK_PROBES
    CodeInfo* ci = codeinfo_get(code);
    PyCodeObject* co = (PyCodeObject*)code;
    dd_monitoring* m = (dd_monitoring*)co->_co_monitoring;
    if (ci == NULL || !g_block_tables || (ci->blocks == NULL && (m == NULL || m->lines == NULL))) {
        PyErr_SetString(PyExc_ValueError, "the code object has no line data yet");
        return NULL;
    }
    BlockInfo* own = NULL;
    BlockInfo* bi = ci->blocks;
    if (bi == NULL) {
        if (ci->starts == NULL && compute_starts(ci, co) < 0)
            return PyErr_NoMemory();
        if (compute_start_lines(ci, co) < 0) {
            PyErr_SetString(PyExc_ValueError, "line table walk failed");
            return NULL;
        }
        own = bi = analyse_blocks(ci, co);
        if (bi == NULL)
            Py_RETURN_NONE;
    }
    PyObject* result = NULL;
    PyObject* blocks = PyList_New(bi->n_blocks);
    PyObject* probes = PyList_New(bi->n_probes);
    PyObject* ranges = PyList_New(bi->n_ranges);
    uint8_t* probed = PyMem_RawCalloc((size_t)bi->n_blocks + 1, 1);
    if (blocks == NULL || probes == NULL || ranges == NULL || probed == NULL)
        goto done;
    for (int32_t k = 0; k < bi->n_probes; k++) {
        if (bi->probe_block[k] >= 0)
            probed[bi->probe_block[k]] = 1;
        PyObject* item = Py_BuildValue("(iii)", bi->probe_index[k], bi->probe_block[k], bi->probe_caught[k]);
        if (item == NULL)
            goto done;
        PyList_SET_ITEM(probes, k, item);
    }
    for (int32_t b = 0; b < bi->n_blocks; b++) {
        PyObject* lines = PyTuple_New(bi->start_lo[b + 1] - bi->start_lo[b]);
        if (lines == NULL)
            goto done;
        for (int32_t s = bi->start_lo[b]; s < bi->start_lo[b + 1]; s++) {
            PyObject* line = PyLong_FromLong(ci->start_lines[s]);
            if (line == NULL) {
                Py_DECREF(lines);
                goto done;
            }
            PyTuple_SET_ITEM(lines, s - bi->start_lo[b], line);
        }
        PyObject* item = Py_BuildValue("(iNii)", bi->first[b], lines, bi->idom[b], probed[b]);
        if (item == NULL)
            goto done;
        PyList_SET_ITEM(blocks, b, item);
    }
    for (int32_t r = 0; r < bi->n_ranges; r++) {
        PyObject* item = Py_BuildValue("(iii)", bi->range_start[r], bi->range_end[r], bi->range_catch[r]);
        if (item == NULL)
            goto done;
        PyList_SET_ITEM(ranges, r, item);
    }
    result = Py_BuildValue("{sOsOsO}", "blocks", blocks, "probes", probes, "ranges", ranges);
done:
    Py_XDECREF(blocks);
    Py_XDECREF(probes);
    Py_XDECREF(ranges);
    PyMem_RawFree(probed);
    PyMem_RawFree(own);
    return result;
#else
    Py_RETURN_NONE;
#endif
}

/* probes(code) -> 0 when not decided or not attached, 1 for LINE events, 2 for INSTRUCTION probes on
   the line starts, 3 for block probes. */
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
    const struct
    {
        const char* name;
        unsigned long long value;
    } stats[] = {
        { "events_init", g_events_init },
        { "events_unowned", g_events_unowned },
        { "events_kept", g_events_kept },
        { "events_instruction", g_events_instruction },
        { "events_block", g_events_block },
        { "events_caught", g_events_caught },
        { "events_unwind", g_events_unwind },
        { "errors", g_errors },
        { "rearm_internal", g_rearm_internal },
        { "rearm_public", g_rearm_public },
        { "rearm_instruction", g_rearm_instruction },
        { "rearm_block", g_rearm_block },
        { "rearm_starts", g_rearm_starts },
        { "switched", g_switched },
        { "switched_block", g_switched_block },
        { "block_refused", g_block_refused },
        { "converted", g_converted },
        { "reverted", g_reverted },
        { "dirty", (unsigned long long)g_n_dirty },
        { "active", (unsigned long long)g_n_active },
        { "files", (unsigned long long)g_n_files },
#if DD_HAVE_BLOCK_PROBES
        { "block_codes", (unsigned long long)g_n_block_codes },
#endif
        { "internal_rearm", (unsigned long long)g_internal_rearm },
        { "cached_owner", (unsigned long long)g_cached_owner },
        { "instruction_probes", (unsigned long long)g_instruction_probes },
        { "block_probes", (unsigned long long)g_block_probes },
    };
    PyObject* d = PyDict_New();
    if (d == NULL)
        return NULL;
    for (size_t k = 0; k < sizeof(stats) / sizeof(stats[0]); k++) {
        PyObject* v = PyLong_FromUnsignedLongLong(stats[k].value);
        if (v == NULL || PyDict_SetItemString(d, stats[k].name, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(d);
            return NULL;
        }
        Py_DECREF(v);
    }
    return d;
}

/* ------------------------------------------------------------------------------------------------
 * Child processes.
 */

/* set_running(context): run in context, a Context or None, and publish it to child processes. */
static PyObject*
mod_set_running(PyObject* Py_UNUSED(self), PyObject* context)
{
    if (g_ctxvar == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "_linecov.init() was not called");
        return NULL;
    }
#if DD_HAVE_BLOCK_PROBES
    Context* old_owner = g_n_active > 0 ? current_owner() : NULL;
    if (old_owner != owner_of(context))
        settle_switch(old_owner);
#endif
    PyObject* token = PyContextVar_Set(g_ctxvar, context);
    if (token == NULL)
        return NULL;
    Py_DECREF(token);
    publish_owner(context);
    Py_RETURN_NONE;
}

static void
unmap_state(void)
{
#if DD_HAVE_SHARED_STATE
    if (g_state != NULL)
        munmap(g_state, sizeof(SharedState));
#endif
    g_state = NULL;
}

/* share_state(path): map the state file shared with child processes. It holds at least a SharedState. */
static PyObject*
mod_share_state(PyObject* Py_UNUSED(self), PyObject* arg)
{
#if DD_HAVE_SHARED_STATE
    PyObject* path = NULL;
    if (!PyUnicode_FSConverter(arg, &path))
        return NULL;
    int fd = open(PyBytes_AS_STRING(path), O_RDWR | O_CLOEXEC);
    Py_DECREF(path);
    if (fd < 0)
        return PyErr_SetFromErrno(PyExc_OSError);
    struct stat st;
    if (fstat(fd, &st) < 0 || st.st_size < (off_t)sizeof(SharedState)) {
        close(fd);
        PyErr_SetString(PyExc_ValueError, "the state file is too short");
        return NULL;
    }
    void* mapped = mmap(NULL, sizeof(SharedState), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (mapped == MAP_FAILED)
        return PyErr_SetFromErrno(PyExc_OSError);
    unmap_state();
    g_state = (SharedState*)mapped;
    Py_RETURN_NONE;
#else
    (void)arg;
    PyErr_SetString(PyExc_NotImplementedError, "child processes are not followed on this platform");
    return NULL;
#endif
}

static PyObject*
mod_unshare_state(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    unmap_state();
    Py_RETURN_NONE;
}

/* owner() -> the token of the context published to child processes, 0 for none. */
static PyObject*
mod_owner(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
#if DD_HAVE_SHARED_STATE
    if (g_state != NULL)
        return PyLong_FromUnsignedLongLong(__atomic_load_n(&g_state->owner, __ATOMIC_RELAXED));
#endif
    return PyLong_FromLong(0);
}

/* records() -> the number of child records written so far. */
static PyObject*
mod_records(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
#if DD_HAVE_SHARED_STATE
    if (g_state != NULL)
        return PyLong_FromUnsignedLongLong(__atomic_load_n(&g_state->records, __ATOMIC_ACQUIRE));
#endif
    return PyLong_FromLong(0);
}

/* set_records_callback(callback): the function a window's __exit__ calls when child processes wrote
   records since it last ran, or None for none. Records already written count as seen. */
static PyObject*
mod_set_records_callback(PyObject* Py_UNUSED(self), PyObject* callback)
{
    if (callback != Py_None && !PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "set_records_callback(callback)");
        return NULL;
    }
    Py_XSETREF(g_records_callback, callback == Py_None ? NULL : Py_NewRef(callback));
#if DD_HAVE_SHARED_STATE
    g_records_seen = g_state != NULL ? __atomic_load_n(&g_state->records, __ATOMIC_ACQUIRE) : 0;
#endif
    Py_RETURN_NONE;
}

/* add_record(): count a record a child wrote, once its file is in place. */
static PyObject*
mod_add_record(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
#if DD_HAVE_SHARED_STATE
    if (g_state != NULL)
        __atomic_fetch_add(&g_state->records, 1, __ATOMIC_RELEASE);
#endif
    Py_RETURN_NONE;
}

/* ORs a bitmap into a bitset. */
static int
bitset_or_bytes(Bitset* b, const uint8_t* bits, Py_ssize_t n)
{
    while (n > 0 && bits[n - 1] == 0)
        n--;
    if (n == 0)
        return 0;
    if (n > UINT32_MAX || bitset_reserve(b, (uint32_t)n) < 0)
        return -1;
    for (Py_ssize_t i = 0; i < n; i++)
        b->bits[i] |= bits[i];
    if ((uint32_t)n > b->used)
        b->used = (uint32_t)n;
    return 0;
}

static int
context_or_bytes(Context* c, int32_t file_id, const uint8_t* bits, Py_ssize_t n)
{
    if (file_id >= c->cap_files && context_reserve_files(c, file_id) < 0)
        return -1;
    Bitset* b = &c->files[file_id];
    int first = b->nbytes == 0;
    if (bitset_or_bytes(b, bits, n) < 0)
        return -1;
    if (first && b->nbytes != 0 && context_touch(c, file_id) < 0) {
        bitset_free(b);
        return -1;
    }
    return 0;
}

/* merge(token, file_id, bitmap) -> whether the lines went to a context: a child's lines join the
   active context with this token, or the file's unowned lines once that context's window has closed. */
static PyObject*
mod_merge(PyObject* Py_UNUSED(self), PyObject* const* args, Py_ssize_t nargs)
{
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "merge(token, file_id, bitmap)");
        return NULL;
    }
    unsigned long long token = PyLong_AsUnsignedLongLong(args[0]);
    if (token == (unsigned long long)-1 && PyErr_Occurred())
        return NULL;
    int32_t file_id;
    if (parse_file_id(args[1], &file_id) < 0)
        return NULL;
    Py_buffer view;
    if (PyObject_GetBuffer(args[2], &view, PyBUF_SIMPLE) < 0)
        return NULL;
    Context* owner = NULL;
    for (int k = 0; token != 0 && k < g_n_active; k++) {
        if (g_active[k]->token == token) {
            owner = g_active[k];
            break;
        }
    }
    int rc = owner != NULL ? context_or_bytes(owner, file_id, (const uint8_t*)view.buf, view.len)
                           : bitset_or_bytes(&g_files[file_id].unowned_lines, (const uint8_t*)view.buf, view.len);
    PyBuffer_Release(&view);
    if (rc < 0)
        return PyErr_NoMemory();
    return PyBool_FromLong(owner != NULL);
}

/* union() -> {report_path: bitmap}: every line the process ran, for files in the workspace. */
static PyObject*
mod_union(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    PyObject* result = PyDict_New();
    if (result == NULL)
        return NULL;
    for (int32_t f = 0; f < g_n_files; f++) {
        FileInfo* fi = &g_files[f];
        if (fi->report_path == Py_None)
            continue;
        const Bitset* parts[3] = { &fi->init_lines, &fi->suite_lines, &fi->unowned_lines };
        uint32_t n = 0;
        for (int k = 0; k < 3; k++)
            if (parts[k]->used > n)
                n = parts[k]->used;
        if (n == 0)
            continue;
        PyObject* bitmap = PyBytes_FromStringAndSize(NULL, n);
        if (bitmap == NULL) {
            Py_DECREF(result);
            return NULL;
        }
        uint8_t* out = (uint8_t*)PyBytes_AS_STRING(bitmap);
        memset(out, 0, n);
        for (int k = 0; k < 3; k++)
            for (uint32_t i = 0; i < parts[k]->used; i++)
                out[i] |= parts[k]->bits[i];
        int rc = PyDict_SetItem(result, fi->report_path, bitmap);
        Py_DECREF(bitmap);
        if (rc < 0) {
            Py_DECREF(result);
            return NULL;
        }
    }
    return result;
}

/* forget(): record from now on as a child process that starts here. Closes every window without
   re-arming and empties the process-wide sets, so union() holds only the lines run after the call.
   Meant for a forked process: a line disabled at the fork is in every context active then, which
   the parent reports, and runs here without an event. Block probes stop for good. */
static PyObject*
mod_forget(PyObject* Py_UNUSED(self), PyObject* Py_UNUSED(ignored))
{
    g_block_probes = 0;
#if DD_HAVE_BLOCK_PROBES
    if (g_n_block_codes > 0)
        settle_codes(NULL);
#endif
    while (g_n_active > 0)
        context_deactivate(g_active[g_n_active - 1], 0);
    for (int32_t f = 0; f < g_n_files; f++) {
        bitset_free(&g_files[f].init_lines);
        bitset_free(&g_files[f].suite_lines);
        bitset_free(&g_files[f].unowned_lines);
    }
    Py_RETURN_NONE;
}

static PyObject* g_exec_callback = NULL;
static int g_exec_hook_added = 0;

/* Hands code to the exec callback, unless it is attached to a file already or was compiled from a
   string. Never raises. */
static void
run_exec_callback(PyObject* code)
{
    if (codeinfo_get(code) != NULL)
        return;
    PyObject* filename = ((PyCodeObject*)code)->co_filename;
    if (PyUnicode_Check(filename) && PyUnicode_GET_LENGTH(filename) > 0 && PyUnicode_READ_CHAR(filename, 0) == '<')
        return;
    /* A strong reference: another thread can replace or clear the callback while it runs. */
    PyObject* callback = Py_NewRef(g_exec_callback);
    PyObject* r = PyObject_CallOneArg(callback, code);
    Py_DECREF(callback);
    if (r == NULL)
        PyErr_Clear();
    else
        Py_DECREF(r);
}

/* Hands the exec callback every code object that exec() or the interpreter's file runner is about
   to run: imported modules, runpy, the main script. Never fails the audited operation. */
static int
exec_audit_hook(const char* event, PyObject* args, void* Py_UNUSED(data))
{
    if (event[0] != 'e' || strcmp(event, "exec") != 0 || g_exec_callback == NULL)
        return 0;
    if (!PyTuple_Check(args) || PyTuple_GET_SIZE(args) < 1 || !PyCode_Check(PyTuple_GET_ITEM(args, 0)))
        return 0;
    run_exec_callback(PyTuple_GET_ITEM(args, 0));
    return 0;
}

/* add_exec_hook(callback): call callback(code) before every code object exec() runs, from now on.
   Audit hooks cannot be removed, and every audited operation pays for one, so it is meant for child
   processes only. */
static PyObject*
mod_add_exec_hook(PyObject* Py_UNUSED(self), PyObject* callback)
{
    if (!PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "add_exec_hook(callback)");
        return NULL;
    }
    if (!g_exec_hook_added) {
        if (PySys_AddAuditHook(exec_audit_hook, NULL) < 0)
            return NULL;
        g_exec_hook_added = 1;
    }
    Py_INCREF(callback);
    Py_XSETREF(g_exec_callback, callback);
    Py_RETURN_NONE;
}

/* set_exec_callback(callback): the function the exec wrapper calls, or None for none. */
static PyObject*
mod_set_exec_callback(PyObject* Py_UNUSED(self), PyObject* callback)
{
    if (callback != Py_None && !PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "set_exec_callback(callback)");
        return NULL;
    }
    Py_XSETREF(g_exec_callback, callback == Py_None ? NULL : Py_NewRef(callback));
    Py_RETURN_NONE;
}

static PyObject* g_original_exec = NULL;
static PyObject* g_exec_wrapper = NULL;

/* exec() as the wrapper's original runs it, after handing each code object to the exec callback. A C
   function pushes no frame, so exec() without globals still runs in its caller's namespace. */
static PyObject*
wrapped_exec(PyObject* Py_UNUSED(builtins), PyObject* const* args, Py_ssize_t nargs, PyObject* kwnames)
{
    if (nargs >= 1 && g_exec_callback != NULL && PyCode_Check(args[0]))
        run_exec_callback(args[0]);
    return PyObject_Vectorcall(g_original_exec, args, (size_t)nargs, kwnames);
}

static PyMethodDef g_exec_wrapper_def = { "exec",
                                          (PyCFunction)(void (*)(void))wrapped_exec,
                                          METH_FASTCALL | METH_KEYWORDS,
                                          NULL };

/* wrap_exec(original) -> function: a replacement for builtins.exec that hands the exec callback each
   code object before original runs it. It sees what every exec() lookup in a module's namespace
   runs, the import system's included, at the cost of one C call per exec(), where an audit hook
   would tax every audited operation. It has original's name, module, signature and documentation.
   The first original is kept for the life of the process. */
static PyObject*
mod_wrap_exec(PyObject* Py_UNUSED(self), PyObject* original)
{
    if (g_exec_wrapper != NULL)
        return Py_NewRef(g_exec_wrapper);
    if (!PyCallable_Check(original)) {
        PyErr_SetString(PyExc_TypeError, "wrap_exec(original)");
        return NULL;
    }
    /* "exec(<signature>)\n--\n\n<doc>" is how a builtin's docstring carries its signature. */
    PyObject* signature = PyObject_GetAttrString(original, "__text_signature__");
    PyObject* doc = PyObject_GetAttrString(original, "__doc__");
    if (signature == NULL || doc == NULL) {
        Py_XDECREF(signature);
        Py_XDECREF(doc);
        return NULL;
    }
    PyObject* text = (PyUnicode_Check(signature) && PyUnicode_Check(doc))
                       ? PyUnicode_FromFormat("exec%U\n--\n\n%U", signature, doc)
                       : PyUnicode_FromString("");
    Py_DECREF(signature);
    Py_DECREF(doc);
    if (text == NULL)
        return NULL;
    Py_ssize_t size;
    const char* utf8 = PyUnicode_AsUTF8AndSize(text, &size);
    char* copy = utf8 == NULL ? NULL : PyMem_RawMalloc((size_t)size + 1);
    if (copy != NULL)
        memcpy(copy, utf8, (size_t)size + 1);
    Py_DECREF(text);
    if (copy == NULL)
        return utf8 == NULL ? NULL : PyErr_NoMemory();
    /* Owned by the function definition, which lives as long as the process. */
    g_exec_wrapper_def.ml_doc = size > 0 ? copy : NULL;
    if (size == 0)
        PyMem_RawFree(copy);
    /* Bound to the builtins module, as exec() is: it then pickles by name, and inspect drops the
       $module parameter of its signature. */
    PyObject* builtins = PyImport_ImportModule("builtins");
    if (builtins == NULL)
        return NULL;
    PyObject* module_name = PyUnicode_FromString("builtins");
    PyObject* wrapper = module_name == NULL ? NULL : PyCFunction_NewEx(&g_exec_wrapper_def, builtins, module_name);
    Py_XDECREF(module_name);
    Py_DECREF(builtins);
    if (wrapper == NULL)
        return NULL;
    g_original_exec = Py_NewRef(original);
    g_exec_wrapper = Py_NewRef(wrapper);
    return wrapper;
}

#if DD_HAVE_SHARED_STATE
/* realpath(path) -> str, or None when the C library cannot resolve it, as for a path that does not
   exist. A child process resolves each directory it imports from, and os.path.realpath costs
   about a hundred microseconds per path. */
static PyObject*
mod_realpath(PyObject* Py_UNUSED(self), PyObject* path)
{
    PyObject* encoded = NULL;
    if (!PyUnicode_FSConverter(path, &encoded))
        return NULL;
    char* resolved;
    Py_BEGIN_ALLOW_THREADS;
    resolved = realpath(PyBytes_AS_STRING(encoded), NULL);
    Py_END_ALLOW_THREADS;
    Py_DECREF(encoded);
    if (resolved == NULL)
        Py_RETURN_NONE;
    PyObject* result = PyUnicode_DecodeFSDefault(resolved);
    free(resolved);
    return result;
}
#endif

static PyMethodDef module_methods[] = {
    { "init", (PyCFunction)(void (*)(void))mod_init, METH_VARARGS | METH_KEYWORDS, "Configure the collector." },
    { "set_running", mod_set_running, METH_O, "Run in a context, and publish it to child processes." },
    { "share_state", mod_share_state, METH_O, "Map the state file shared with child processes." },
    { "unshare_state", mod_unshare_state, METH_NOARGS, "Unmap the state file." },
    { "owner", mod_owner, METH_NOARGS, "The token of the context published to child processes." },
    { "records", mod_records, METH_NOARGS, "The number of child records written." },
    { "set_records_callback",
      mod_set_records_callback,
      METH_O,
      "Set the function window exits call when child processes wrote records." },
    { "add_record", mod_add_record, METH_NOARGS, "Count a record this child wrote." },
    { "merge", (PyCFunction)(void (*)(void))mod_merge, METH_FASTCALL, "Merge a child's lines." },
    { "union", mod_union, METH_NOARGS, "Every line run, by report path." },
    { "forget", mod_forget, METH_NOARGS, "Record from now on as a child process that starts here." },
    { "add_exec_hook", mod_add_exec_hook, METH_O, "Call a function with every code object exec() runs." },
    { "set_exec_callback", mod_set_exec_callback, METH_O, "Set the function the exec wrapper calls." },
    { "wrap_exec", mod_wrap_exec, METH_O, "A builtins.exec that calls the exec callback first." },
#if DD_HAVE_SHARED_STATE
    { "realpath", mod_realpath, METH_O, "The C library's realpath(), or None when it fails." },
#endif
    { "set_internal_rearm", mod_set_internal_rearm, METH_O, "Switch the internal re-arm." },
    { "set_instruction_probes", mod_set_instruction_probes, METH_O, "Switch INSTRUCTION probes." },
    { "set_block_probes", mod_set_block_probes, METH_O, "Switch block probes." },
    { "set_cached_owner", mod_set_cached_owner, METH_O, "Switch the owner lookup's cache read." },
    { "disable_blocks", mod_disable_blocks, METH_NOARGS, "Stop block probes for good." },
    { "blocks", mod_blocks, METH_O, "The block analysis of a code object." },
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
    /* The sys.monitoring LINE, INSTRUCTION and PY_UNWIND callbacks. */
    if (add_callback(m, "line_event", line_event) < 0
#if DD_HAVE_INSTRUCTION_PROBES
        || add_callback(m, "instruction_event", instruction_event) < 0
#endif
#if DD_HAVE_BLOCK_PROBES
        || add_callback(m, "unwind_event", unwind_event) < 0
#endif
    ) {
        Py_DECREF(m);
        return NULL;
    }
    if (PyModule_AddIntConstant(m, "HAVE_INTERNAL_REARM", DD_HAVE_INTERNAL_REARM) < 0 ||
        PyModule_AddIntConstant(m, "HAVE_INSTRUCTION_PROBES", DD_HAVE_INSTRUCTION_PROBES) < 0 ||
        PyModule_AddIntConstant(m, "HAVE_BLOCK_PROBES", DD_HAVE_BLOCK_PROBES) < 0 ||
        PyModule_AddIntConstant(m, "FREE_THREADED", DD_LINECOV_FREE_THREADED) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
