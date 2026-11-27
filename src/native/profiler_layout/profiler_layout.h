/*
 * Read-only layout of the native tracer objects, for the stack profiler.
 *
 * The native module exports a pointer to a dd_profiler_layout_t in the capsule
 * "ddtrace.internal.native._native._profiler_layout". It lets a reader that holds no GIL and
 * calls no Python API find the active span: the DD_CONTEXTVAR key in a contextvars.Context,
 * then the fields of the SpanData and Context objects it holds.
 *
 * Check version and size before reading anything else. The descriptor never changes after
 * module init, except span_type, which is set once (load it with acquire ordering).
 *
 * Offsets are in bytes from the start of the Python object. Fields marked atomic are written
 * with release stores; read them with acquire loads. Other fields are set before the object is
 * published and never change afterwards. A reader never follows a string pointer from a span:
 * span types are ids into the span type table.
 *
 * The Rust definitions are in src/native/profiler_layout/mod.rs and span_types.rs. Keep both in
 * sync and bump DD_PROFILER_LAYOUT_VERSION on any change.
 */
#ifndef DDTRACE_PROFILER_LAYOUT_H
#define DDTRACE_PROFILER_LAYOUT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

#define DD_PROFILER_LAYOUT_VERSION 1
#define DD_PROFILER_LAYOUT_CAPSULE "ddtrace.internal.native._native._profiler_layout"

/* Span type id for "no span type" (None or the empty string). */
#define DD_SPAN_TYPE_NONE 0u

    /* Byte offsets in a SpanData object, including ddtrace._trace.span.Span and other subclasses. */
    typedef struct
    {
        /* One copy of copy_size bytes from copy_offset holds every field below. */
        uint32_t copy_offset;
        uint32_t copy_size;
        uint32_t span_id;        /* uint64_t */
        uint32_t parent_id;      /* uint64_t, 0 when the span has no parent id */
        uint32_t parent;         /* PyObject*, the parent span, NULL for none */
        uint32_t local_root;     /* PyObject*, the local root span, NULL when the span is its own */
        uint32_t parent_context; /* PyObject*, the parent Context, NULL for none */
        uint32_t span_type_id;   /* atomic uint32_t, id in the span type table */
        uint32_t finished;       /* atomic uint8_t, 1 when the span has finished */
        uint32_t _reserved;
    } dd_span_offsets_t;

    /* Byte offsets in a Context object. */
    typedef struct
    {
        /* One copy of copy_size bytes from copy_offset holds every field below. */
        uint32_t copy_offset;
        uint32_t copy_size;
        uint32_t span_id;            /* atomic uint64_t, low 64 bits of span_id, 0 when None */
        uint32_t local_root_span_id; /* atomic uint64_t, propagated local root span id, 0 when absent */
        uint32_t span_type_id;       /* atomic uint32_t, propagated span type id, 0 when absent */
        uint32_t reactivate;         /* atomic uint8_t, Context._reactivate */
    } dd_context_offsets_t;

    /* One span type. data points to len bytes of UTF-8 followed by a NUL byte. */
    typedef struct
    {
        const char* data;
        uint32_t len;
        uint32_t _reserved;
    } dd_span_type_entry_t;

    /*
     * Append-only table of span types. entries[id] is valid for id < *count; load *count with
     * acquire ordering. Entries never change once published and are never freed.
     */
    typedef struct
    {
        const dd_span_type_entry_t* entries;
        const uint32_t* count; /* atomic */
        uint32_t capacity;
        uint32_t unavailable_id; /* stored when the table is full; drop the span type label */
    } dd_span_type_table_t;

    typedef struct
    {
        uint32_t version; /* DD_PROFILER_LAYOUT_VERSION */
        uint32_t size;    /* sizeof(dd_profiler_layout_t) */

        const void* contextvar;        /* the DD_CONTEXTVAR object; compare HAMT keys by identity */
        intptr_t contextvar_hash;      /* hash(DD_CONTEXTVAR), a Py_hash_t */
        uint32_t contextvar_hamt_hash; /* the 32-bit key hash the HAMT uses for DD_CONTEXTVAR */
        uint32_t _reserved;

        const void* span_data_type; /* ddtrace.internal.native._native.SpanData */
        const void* span_type;      /* ddtrace._trace.span.Span, NULL until imported; atomic */
        const void* context_type;   /* ddtrace._trace.context.Context */

        dd_span_offsets_t span;
        dd_context_offsets_t context;
        dd_span_type_table_t span_types;
    } dd_profiler_layout_t;

#if UINTPTR_MAX == 0xffffffffffffffffu
#ifdef __cplusplus
    static_assert(sizeof(dd_profiler_layout_t) == 144, "dd_profiler_layout_t size");
#else
    _Static_assert(sizeof(dd_profiler_layout_t) == 144, "dd_profiler_layout_t size");
#endif
#endif

#ifdef __cplusplus
}
#endif

#endif /* DDTRACE_PROFILER_LAYOUT_H */
