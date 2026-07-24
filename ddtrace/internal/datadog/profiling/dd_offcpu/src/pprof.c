#include "pprof.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <zlib.h>

/*
 * pprof model + a tiny protobuf serializer. Field numbers and wire types follow
 * profile.proto:
 *   Profile  { sample_type=1, sample=2, location=4, function=5, string_table=6,
 *              time_nanos=9, duration_nanos=10, period_type=11, period=12 }
 *   ValueType{ type=1, unit=2 }
 *   Sample   { location_id=1 (packed), value=2 (packed), label=3 }
 *   Label    { key=1, str=2, num=3 }
 *   Location { id=1, line=4 }
 *   Line     { function_id=1, line=2 }
 *   Function { id=1, name=2, system_name=3, filename=4, start_line=5 }
 */

/* ----------------------------------------------------------- growable buffer */

struct buf
{
    uint8_t* data;
    size_t len;
    size_t cap;
};

static int
buf_reserve(struct buf* b, size_t extra)
{
    if (b->len + extra <= b->cap)
        return 0;
    size_t ncap = b->cap ? b->cap : 256;
    while (ncap < b->len + extra)
        ncap *= 2;
    uint8_t* nd = realloc(b->data, ncap);
    if (nd == NULL)
        return -1;
    b->data = nd;
    b->cap = ncap;
    return 0;
}

static void
buf_bytes(struct buf* b, const void* p, size_t n)
{
    if (buf_reserve(b, n) != 0)
        return;
    memcpy(b->data + b->len, p, n);
    b->len += n;
}

static void
buf_varint(struct buf* b, uint64_t v)
{
    uint8_t tmp[10];
    size_t i = 0;
    do {
        uint8_t byte = v & 0x7f;
        v >>= 7;
        if (v != 0)
            byte |= 0x80;
        tmp[i++] = byte;
    } while (v != 0);
    buf_bytes(b, tmp, i);
}

static void
buf_tag(struct buf* b, int field, int wire)
{
    buf_varint(b, ((uint64_t)field << 3) | (uint64_t)wire);
}

static void
buf_varint_field(struct buf* b, int field, uint64_t v)
{
    buf_tag(b, field, 0);
    buf_varint(b, v);
}

static void
buf_bytes_field(struct buf* b, int field, const void* p, size_t n)
{
    buf_tag(b, field, 2);
    buf_varint(b, n);
    buf_bytes(b, p, n);
}

/* --------------------------------------------------------------- pprof model */

struct func
{
    uint32_t name; /* string table index */
    uint32_t file; /* string table index */
    int32_t line;
};

struct sample
{
    uint64_t* locs;
    int nlocs;
    int64_t value;
    uint32_t tid;
    uint32_t comm; /* string table index */
};

struct pprof
{
    char** strings;
    size_t nstrings, capstrings;

    struct func* funcs;
    size_t nfuncs, capfuncs;

    struct sample* samples;
    size_t nsamples, capsamples;

    uint32_t st_type, st_unit;     /* sample type / unit string indices  */
    uint32_t key_tid, key_comm;    /* label key string indices           */
    struct timespec start;
};

static uint32_t
intern_string(struct pprof* p, const char* s)
{
    for (size_t i = 0; i < p->nstrings; i++)
        if (strcmp(p->strings[i], s) == 0)
            return (uint32_t)i;
    if (p->nstrings == p->capstrings) {
        size_t nc = p->capstrings ? p->capstrings * 2 : 64;
        char** ns = realloc(p->strings, nc * sizeof(*ns));
        if (ns == NULL)
            return 0;
        p->strings = ns;
        p->capstrings = nc;
    }
    p->strings[p->nstrings] = strdup(s);
    return (uint32_t)p->nstrings++;
}

struct pprof*
pprof_new(const char* sample_type, const char* unit)
{
    struct pprof* p = calloc(1, sizeof(*p));
    if (p == NULL)
        return NULL;
    clock_gettime(CLOCK_MONOTONIC, &p->start);
    intern_string(p, ""); /* string_table[0] must be empty */
    p->st_type = intern_string(p, sample_type);
    p->st_unit = intern_string(p, unit);
    p->key_tid = intern_string(p, "thread id");
    p->key_comm = intern_string(p, "thread name");
    return p;
}

void
pprof_free(struct pprof* p)
{
    if (p == NULL)
        return;
    for (size_t i = 0; i < p->nstrings; i++)
        free(p->strings[i]);
    free(p->strings);
    free(p->funcs);
    for (size_t i = 0; i < p->nsamples; i++)
        free(p->samples[i].locs);
    free(p->samples);
    free(p);
}

uint64_t
pprof_intern_frame(struct pprof* p, const char* name, const char* file, int line)
{
    uint32_t nidx = intern_string(p, name != NULL ? name : "");
    uint32_t fidx = intern_string(p, file != NULL ? file : "");
    for (size_t i = 0; i < p->nfuncs; i++)
        if (p->funcs[i].name == nidx && p->funcs[i].file == fidx && p->funcs[i].line == line)
            return (uint64_t)(i + 1); /* location id == function id (1-based) */

    if (p->nfuncs == p->capfuncs) {
        size_t nc = p->capfuncs ? p->capfuncs * 2 : 64;
        struct func* nf = realloc(p->funcs, nc * sizeof(*nf));
        if (nf == NULL)
            return 0;
        p->funcs = nf;
        p->capfuncs = nc;
    }
    p->funcs[p->nfuncs].name = nidx;
    p->funcs[p->nfuncs].file = fidx;
    p->funcs[p->nfuncs].line = line;
    return (uint64_t)(++p->nfuncs); /* new id */
}

void
pprof_add_sample(struct pprof* p, const uint64_t* locs, int nlocs, int64_t value, uint32_t tid, const char* comm)
{
    if (p->nsamples == p->capsamples) {
        size_t nc = p->capsamples ? p->capsamples * 2 : 256;
        struct sample* ns = realloc(p->samples, nc * sizeof(*ns));
        if (ns == NULL)
            return;
        p->samples = ns;
        p->capsamples = nc;
    }
    struct sample* s = &p->samples[p->nsamples];
    s->locs = malloc((size_t)nlocs * sizeof(uint64_t));
    if (s->locs == NULL)
        return;
    memcpy(s->locs, locs, (size_t)nlocs * sizeof(uint64_t));
    s->nlocs = nlocs;
    s->value = value;
    s->tid = tid;
    s->comm = intern_string(p, comm != NULL ? comm : "");
    p->nsamples++;
}

/* ----------------------------------------------------------------- serialize */

static void
encode_value_type(struct buf* out, int field, uint32_t type, uint32_t unit)
{
    struct buf m = {0};
    buf_varint_field(&m, 1, type);
    buf_varint_field(&m, 2, unit);
    buf_bytes_field(out, field, m.data, m.len);
    free(m.data);
}

static void
encode_sample(struct buf* out, const struct sample* s, uint32_t key_tid, uint32_t key_comm)
{
    struct buf m = {0};

    /* location_id: packed repeated */
    struct buf locs = {0};
    for (int i = 0; i < s->nlocs; i++)
        buf_varint(&locs, s->locs[i]);
    buf_bytes_field(&m, 1, locs.data, locs.len);
    free(locs.data);

    /* value: packed repeated (single value) */
    struct buf vals = {0};
    buf_varint(&vals, (uint64_t)s->value);
    buf_bytes_field(&m, 2, vals.data, vals.len);
    free(vals.data);

    /* label: thread id (num) */
    struct buf l1 = {0};
    buf_varint_field(&l1, 1, key_tid);
    buf_varint_field(&l1, 3, s->tid);
    buf_bytes_field(&m, 3, l1.data, l1.len);
    free(l1.data);

    /* label: thread name (str) */
    struct buf l2 = {0};
    buf_varint_field(&l2, 1, key_comm);
    buf_varint_field(&l2, 2, s->comm);
    buf_bytes_field(&m, 3, l2.data, l2.len);
    free(l2.data);

    buf_bytes_field(out, 2, m.data, m.len);
    free(m.data);
}

static void
encode_location(struct buf* out, uint64_t id, const struct func* fn)
{
    struct buf m = {0};
    buf_varint_field(&m, 1, id);
    /* line { function_id, line } */
    struct buf line = {0};
    buf_varint_field(&line, 1, id); /* function id == location id */
    buf_varint_field(&line, 2, (uint64_t)(fn->line > 0 ? fn->line : 0));
    buf_bytes_field(&m, 4, line.data, line.len);
    free(line.data);
    buf_bytes_field(out, 4, m.data, m.len);
    free(m.data);
}

static void
encode_function(struct buf* out, uint64_t id, const struct func* fn)
{
    struct buf m = {0};
    buf_varint_field(&m, 1, id);
    buf_varint_field(&m, 2, fn->name); /* name */
    /* Deliberately leave system_name (field 3) empty. `go tool pprof` runs C++
     * demangling on any function where name == system_name, and demangle.Filter
     * turns Python synthetic names like "<module>"/"<listcomp>" into an empty
     * string. Keeping system_name != name trips pprof's "already demangled"
     * guard so our names are displayed verbatim. */
    buf_varint_field(&m, 4, fn->file); /* filename */
    buf_varint_field(&m, 5, (uint64_t)(fn->line > 0 ? fn->line : 0));
    buf_bytes_field(out, 5, m.data, m.len);
    free(m.data);
}

static int
gzip_write(const char* path, const uint8_t* data, size_t len)
{
    gzFile f = gzopen(path, "wb");
    if (f == NULL)
        return -1;
    int ok = 0;
    size_t off = 0;
    while (off < len) {
        size_t chunk = len - off;
        if (chunk > (1u << 20))
            chunk = (1u << 20);
        int w = gzwrite(f, data + off, (unsigned)chunk);
        if (w <= 0) {
            ok = -1;
            break;
        }
        off += (size_t)w;
    }
    gzclose(f);
    return ok;
}

int
pprof_write(struct pprof* p, const char* path)
{
    struct buf out = {0};

    /* sample_type (1) and period_type (11) */
    encode_value_type(&out, 1, p->st_type, p->st_unit);

    /* samples (2) */
    for (size_t i = 0; i < p->nsamples; i++)
        encode_sample(&out, &p->samples[i], p->key_tid, p->key_comm);

    /* locations (4) and functions (5), ids 1..nfuncs */
    for (size_t i = 0; i < p->nfuncs; i++)
        encode_location(&out, (uint64_t)(i + 1), &p->funcs[i]);
    for (size_t i = 0; i < p->nfuncs; i++)
        encode_function(&out, (uint64_t)(i + 1), &p->funcs[i]);

    /* string_table (6) */
    for (size_t i = 0; i < p->nstrings; i++)
        buf_bytes_field(&out, 6, p->strings[i], strlen(p->strings[i]));

    /* time/duration */
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    buf_varint_field(&out, 9, (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec);

    struct timespec end;
    clock_gettime(CLOCK_MONOTONIC, &end);
    int64_t dur = (int64_t)(end.tv_sec - p->start.tv_sec) * 1000000000ll + (end.tv_nsec - p->start.tv_nsec);
    if (dur < 0)
        dur = 0;
    buf_varint_field(&out, 10, (uint64_t)dur);

    /* period_type (11) + period (12) */
    encode_value_type(&out, 11, p->st_type, p->st_unit);
    buf_varint_field(&out, 12, 1);

    int rc = gzip_write(path, out.data, out.len);
    free(out.data);
    return rc;
}
