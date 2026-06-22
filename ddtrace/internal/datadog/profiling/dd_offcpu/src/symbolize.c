#include "symbolize.h"

#include "symbolize_internal.h"

#include <fcntl.h>
#include <gelf.h>
#include <libelf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * Native ELF symbolizer.
 *
 * Resolution is purely file-offset based, which makes it uniform across
 * position-independent objects (ET_DYN: shared libraries and PIE executables)
 * and fixed-load executables (ET_EXEC):
 *
 *   1. /proc/<pid>/maps gives, for each executable file-backed mapping, the
 *      runtime range [start, end) and the file offset that `start` maps to.
 *   2. A runtime address A in that mapping corresponds to file offset
 *      F = A - start + map_offset.
 *   3. Each ELF symbol's st_value (a virtual address) is converted once to a
 *      file offset using the PT_LOAD segment that contains it
 *      (foff = st_value - p_vaddr + p_offset). Symbols are sorted by file
 *      offset, so resolving A is a binary search for the greatest symbol file
 *      offset <= F.
 *
 * Going through file offsets sidesteps ASLR/load-bias entirely: the same
 * arithmetic is correct whether st_value is absolute (ET_EXEC) or relative
 * (ET_DYN).
 */

struct sym
{
    uint64_t foff; /* symbol start as a file offset      */
    uint64_t size; /* st_size (0 if unknown)             */
    char* name;    /* owned, demangled name not attempted */
};

struct elf_obj
{
    char* path;
    struct sym* syms;
    size_t n;
    int failed; /* could not be opened/parsed */
};

struct mapping
{
    uint64_t start;
    uint64_t end;
    uint64_t offset;
    struct elf_obj* obj;
};

struct symbolizer
{
    pid_t pid;
    struct mapping* maps;
    size_t nmaps;
    struct elf_obj** objs;
    size_t nobjs;
};

/* ------------------------------------------------------------------ helpers */

static int
sym_cmp(const void* a, const void* b)
{
    uint64_t fa = ((const struct sym*)a)->foff;
    uint64_t fb = ((const struct sym*)b)->foff;
    if (fa < fb)
        return -1;
    if (fa > fb)
        return 1;
    return 0;
}

/* Map a symbol's virtual address to a file offset via the PT_LOAD segment that
 * contains it. Returns UINT64_MAX if no segment matches. Exposed (non-static)
 * for unit testing via symbolize_internal.h. */
uint64_t
ddoffcpu_vaddr_to_foff(const GElf_Phdr* loads, size_t nloads, uint64_t vaddr)
{
    for (size_t i = 0; i < nloads; i++) {
        const GElf_Phdr* p = &loads[i];
        if (vaddr >= p->p_vaddr && vaddr < p->p_vaddr + p->p_memsz)
            return vaddr - p->p_vaddr + p->p_offset;
    }
    return UINT64_MAX;
}

static void
obj_add_sym(struct elf_obj* o, size_t* cap, uint64_t foff, uint64_t size, const char* name)
{
    if (o->n == *cap) {
        size_t ncap = *cap ? *cap * 2 : 256;
        struct sym* ns = realloc(o->syms, ncap * sizeof(*ns));
        if (ns == NULL)
            return;
        o->syms = ns;
        *cap = ncap;
    }
    o->syms[o->n].foff = foff;
    o->syms[o->n].size = size;
    o->syms[o->n].name = strdup(name);
    if (o->syms[o->n].name != NULL)
        o->n++;
}

/* Read .symtab and .dynsym FUNC symbols from one ELF object into o. */
static void
obj_load(struct elf_obj* o)
{
    int fd = open(o->path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        o->failed = 1;
        return;
    }

    Elf* elf = elf_begin(fd, ELF_C_READ_MMAP, NULL);
    if (elf == NULL || elf_kind(elf) != ELF_K_ELF) {
        o->failed = 1;
        if (elf != NULL)
            elf_end(elf);
        close(fd);
        return;
    }

    /* Collect PT_LOAD segments for vaddr -> file offset conversion. */
    size_t phnum = 0;
    GElf_Phdr* loads = NULL;
    size_t nloads = 0;
    if (elf_getphdrnum(elf, &phnum) == 0 && phnum > 0) {
        loads = calloc(phnum, sizeof(*loads));
        for (size_t i = 0; loads != NULL && i < phnum; i++) {
            GElf_Phdr ph;
            if (gelf_getphdr(elf, (int)i, &ph) == &ph && ph.p_type == PT_LOAD)
                loads[nloads++] = ph;
        }
    }

    size_t cap = 0;
    Elf_Scn* scn = NULL;
    while ((scn = elf_nextscn(elf, scn)) != NULL) {
        GElf_Shdr sh;
        if (gelf_getshdr(scn, &sh) != &sh)
            continue;
        if (sh.sh_type != SHT_SYMTAB && sh.sh_type != SHT_DYNSYM)
            continue;
        if (sh.sh_entsize == 0)
            continue;

        Elf_Data* data = elf_getdata(scn, NULL);
        if (data == NULL)
            continue;
        size_t count = sh.sh_size / sh.sh_entsize;
        for (size_t j = 0; j < count; j++) {
            GElf_Sym sym;
            if (gelf_getsym(data, (int)j, &sym) != &sym)
                continue;
            if (GELF_ST_TYPE(sym.st_info) != STT_FUNC || sym.st_value == 0)
                continue;
            const char* name = elf_strptr(elf, sh.sh_link, sym.st_name);
            if (name == NULL || name[0] == '\0')
                continue;
            uint64_t foff = ddoffcpu_vaddr_to_foff(loads, nloads, sym.st_value);
            if (foff == UINT64_MAX)
                continue;
            obj_add_sym(o, &cap, foff, sym.st_size, name);
        }
    }

    free(loads);
    elf_end(elf);
    close(fd);

    if (o->n == 0) {
        o->failed = 1;
        return;
    }
    qsort(o->syms, o->n, sizeof(*o->syms), sym_cmp);
}

static struct elf_obj*
sym_get_obj(struct symbolizer* s, const char* path)
{
    for (size_t i = 0; i < s->nobjs; i++) {
        if (strcmp(s->objs[i]->path, path) == 0)
            return s->objs[i];
    }

    struct elf_obj* o = calloc(1, sizeof(*o));
    if (o == NULL)
        return NULL;
    o->path = strdup(path);
    if (o->path == NULL) {
        free(o);
        return NULL;
    }
    obj_load(o);

    struct elf_obj** no = realloc(s->objs, (s->nobjs + 1) * sizeof(*no));
    if (no == NULL) {
        free(o->path);
        free(o);
        return NULL;
    }
    s->objs = no;
    s->objs[s->nobjs++] = o;
    return o;
}

static void
sym_add_map(struct symbolizer* s, uint64_t start, uint64_t end, uint64_t offset, struct elf_obj* obj)
{
    struct mapping* nm = realloc(s->maps, (s->nmaps + 1) * sizeof(*nm));
    if (nm == NULL)
        return;
    s->maps = nm;
    s->maps[s->nmaps].start = start;
    s->maps[s->nmaps].end = end;
    s->maps[s->nmaps].offset = offset;
    s->maps[s->nmaps].obj = obj;
    s->nmaps++;
}

/* Parse one /proc/<pid>/maps line. Exposed (non-static) for unit testing via
 * symbolize_internal.h. See that header for the contract. */
int
ddoffcpu_parse_maps_line(const char* line, uint64_t* start, uint64_t* end, uint64_t* offset, char* path,
                         size_t pathcap)
{
    unsigned long s, e, off;
    char perms[8];
    int pathpos = 0;
    /* "start-end perms offset dev:dev inode pathname" */
    if (sscanf(line, "%lx-%lx %7s %lx %*x:%*x %*u %n", &s, &e, perms, &off, &pathpos) < 4)
        return 0;
    if (strlen(perms) < 3 || perms[2] != 'x') /* executable mappings only */
        return 0;

    const char* p = line + pathpos;
    while (*p == ' ')
        p++;
    if (*p != '/') /* file-backed, real path only (skips "", "[heap]", ...) */
        return 0;
    if (path == NULL || pathcap == 0)
        return 0;

    size_t i = 0;
    for (; p[i] != '\0' && p[i] != '\n' && i + 1 < pathcap; i++)
        path[i] = p[i];
    path[i] = '\0';

    *start = s;
    *end = e;
    *offset = off;
    return 1;
}

/* ------------------------------------------------------------------- public */

struct symbolizer*
symbolizer_new(pid_t pid)
{
    if (elf_version(EV_CURRENT) == EV_NONE)
        return NULL;

    struct symbolizer* s = calloc(1, sizeof(*s));
    if (s == NULL)
        return NULL;
    s->pid = pid;

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE* f = fopen(path, "r");
    if (f == NULL)
        return s; /* usable but empty; resolve will fall back to hex */

    char line[4096];
    while (fgets(line, sizeof(line), f) != NULL) {
        uint64_t start, end, offset;
        char path[4096];
        if (!ddoffcpu_parse_maps_line(line, &start, &end, &offset, path, sizeof(path)))
            continue;

        struct elf_obj* obj = sym_get_obj(s, path);
        if (obj != NULL)
            sym_add_map(s, start, end, offset, obj);
    }
    fclose(f);
    return s;
}

void
symbolizer_free(struct symbolizer* s)
{
    if (s == NULL)
        return;
    for (size_t i = 0; i < s->nobjs; i++) {
        struct elf_obj* o = s->objs[i];
        for (size_t j = 0; j < o->n; j++)
            free(o->syms[j].name);
        free(o->syms);
        free(o->path);
        free(o);
    }
    free(s->objs);
    free(s->maps);
    free(s);
}

static const char*
basename_of(const char* path)
{
    const char* slash = strrchr(path, '/');
    return slash != NULL ? slash + 1 : path;
}

int
symbolizer_resolve(struct symbolizer* s, uint64_t addr, char* buf, size_t buflen)
{
    if (s == NULL || buflen == 0)
        return -1;

    const struct mapping* m = NULL;
    for (size_t i = 0; i < s->nmaps; i++) {
        if (addr >= s->maps[i].start && addr < s->maps[i].end) {
            m = &s->maps[i];
            break;
        }
    }
    if (m == NULL) {
        snprintf(buf, buflen, "0x%llx", (unsigned long long)addr);
        return -1;
    }

    struct elf_obj* o = m->obj;
    uint64_t file_offset = addr - m->start + m->offset;

    if (!o->failed && o->n > 0) {
        /* greatest symbol whose file offset <= file_offset */
        size_t lo = 0, hi = o->n;
        while (lo < hi) {
            size_t mid = lo + (hi - lo) / 2;
            if (o->syms[mid].foff <= file_offset)
                lo = mid + 1;
            else
                hi = mid;
        }
        if (lo > 0) {
            const struct sym* hit = &o->syms[lo - 1];
            uint64_t delta = file_offset - hit->foff;
            if (delta == 0)
                snprintf(buf, buflen, "%s", hit->name);
            else
                snprintf(buf, buflen, "%s+0x%llx", hit->name, (unsigned long long)delta);
            return 0;
        }
    }

    /* In a known executable mapping but no symbol (stripped object): still
     * useful to show which module and where. This is a real frame, so return
     * success even though we lack a name. */
    snprintf(buf, buflen, "%s+0x%llx", basename_of(o->path), (unsigned long long)file_offset);
    return 0;
}
