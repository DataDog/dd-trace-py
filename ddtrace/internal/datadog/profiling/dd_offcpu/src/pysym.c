#define _GNU_SOURCE
#include "pysym.h"

#include <fcntl.h>
#include <gelf.h>
#include <libelf.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>
#include <unistd.h>

/*
 * Out-of-process CPython frame walker.
 *
 * Reads the target's memory with process_vm_readv and walks:
 *   _PyRuntime -> interpreters.head -> threads.head (match native_thread_id)
 *              -> cframe.current_frame -> _PyInterpreterFrame chain
 *              -> PyCodeObject (co_qualname, co_filename, co_firstlineno)
 *
 * Struct offsets are CPython-version-specific. This spike targets 3.12; the
 * offsets below were generated from the interpreter's own internal headers
 * (offsetof against pycore_runtime.h / pycore_interp.h / pycore_frame.h on
 * 3.12.10). Other versions are detected and declined rather than misread. A
 * production version would carry a per-(major,minor) offset table the way
 * py-spy and echion do.
 */

/* ---- CPython 3.12 offsets (bytes) ---- */
#define PY312_RUNTIME_INTERP_HEAD 40
#define PY312_INTERP_THREADS_HEAD 72
#define PY312_INTERP_NEXT 0
#define PY312_TSTATE_NEXT 8
#define PY312_TSTATE_NATIVE_TID 144
#define PY312_TSTATE_CFRAME 56
#define PY312_CFRAME_CURRENT_FRAME 0
#define PY312_FRAME_F_CODE 0
#define PY312_FRAME_PREVIOUS 8
#define PY312_FRAME_OWNER 70
#define PY312_CODE_FILENAME 112
#define PY312_CODE_NAME 120
#define PY312_CODE_QUALNAME 128
#define PY312_CODE_FIRSTLINENO 68

/* PyUnicode (compact) data offsets. */
#define PY_ASCII_LENGTH 16
#define PY_ASCII_STATE 32
#define PY_ASCII_DATA 40   /* sizeof(PyASCIIObject): compact-ASCII payload here */
#define PY_COMPACT_DATA 56 /* sizeof(PyCompactUnicodeObject): non-ASCII payload */

/* _PyFrameOwner: frames owned by the C stack are trampoline shims with no
 * meaningful Python code; they are skipped during the walk. */
#define FRAME_OWNED_BY_CSTACK 3

#define MAX_FRAME_WALK 256

struct pysym
{
    pid_t pid;
    int py_minor;       /* CPython minor version (e.g. 12)        */
    int supported;      /* offsets known for this version         */
    uint64_t runtime;   /* runtime address of _PyRuntime          */
};

/* ----------------------------------------------------------- memory reads */

static int
read_mem(const struct pysym* py, uint64_t addr, void* buf, size_t len)
{
    if (addr == 0)
        return -1;
    struct iovec local = {.iov_base = buf, .iov_len = len};
    struct iovec remote = {.iov_base = (void*)(uintptr_t)addr, .iov_len = len};
    ssize_t n = process_vm_readv(py->pid, &local, 1, &remote, 1, 0);
    return (n == (ssize_t)len) ? 0 : -1;
}

static uint64_t
read_ptr(const struct pysym* py, uint64_t addr)
{
    uint64_t v = 0;
    if (read_mem(py, addr, &v, sizeof(v)) != 0)
        return 0;
    return v;
}

/*
 * Read a CPython str object into buf (NUL-terminated). Handles the common
 * compact representations; multibyte kinds are read best-effort (names and
 * filenames are practically always 1-byte). Returns 0 on success.
 */
static int
read_pystr(const struct pysym* py, uint64_t addr, char* buf, size_t buflen)
{
    if (addr == 0 || buflen == 0)
        return -1;

    int64_t length = 0;
    uint32_t state = 0;
    if (read_mem(py, addr + PY_ASCII_LENGTH, &length, sizeof(length)) != 0)
        return -1;
    if (read_mem(py, addr + PY_ASCII_STATE, &state, sizeof(state)) != 0)
        return -1;
    if (length <= 0)
        return -1;

    unsigned ascii = (state >> 6) & 1u;
    uint64_t data = addr + (ascii ? PY_ASCII_DATA : PY_COMPACT_DATA);

    size_t n = (size_t)length;
    if (n > buflen - 1)
        n = buflen - 1;
    if (read_mem(py, data, buf, n) != 0)
        return -1;
    buf[n] = '\0';
    return 0;
}

/* ------------------------------------------------------- interpreter discovery */

/* Find symbol `name` in an ELF file; returns its st_value (a vaddr) and the
 * ELF type via *et. Returns 1 if found. */
static int
elf_find_symbol(const char* path, const char* name, uint64_t* vaddr, int* et)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    Elf* elf = elf_begin(fd, ELF_C_READ_MMAP, NULL);
    if (elf == NULL || elf_kind(elf) != ELF_K_ELF) {
        if (elf != NULL)
            elf_end(elf);
        close(fd);
        return 0;
    }

    GElf_Ehdr ehdr;
    *et = (gelf_getehdr(elf, &ehdr) == &ehdr) ? ehdr.e_type : ET_NONE;

    int found = 0;
    Elf_Scn* scn = NULL;
    while (!found && (scn = elf_nextscn(elf, scn)) != NULL) {
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
        for (size_t i = 0; i < count; i++) {
            GElf_Sym sym;
            if (gelf_getsym(data, (int)i, &sym) != &sym)
                continue;
            const char* sname = elf_strptr(elf, sh.sh_link, sym.st_name);
            /* Match only a *defined* symbol. Extension modules (e.g.
             * _socket/_lzma in lib-dynload) carry an UNDEF `_PyRuntime` import
             * with st_value 0, and they are often mapped before libpython; a
             * name-only match would resolve _PyRuntime to such an object's base
             * and read garbage. Skip undefined/zero-value entries. */
            if (sname != NULL && strcmp(sname, name) == 0 &&
                sym.st_shndx != SHN_UNDEF && sym.st_value != 0) {
                *vaddr = sym.st_value;
                found = 1;
                break;
            }
        }
    }

    elf_end(elf);
    close(fd);
    return found;
}

/* Parse "python3.NN" / "libpython3.NN" out of a path. Returns minor or -1. */
static int
parse_py_minor(const char* path)
{
    const char* p = path;
    while ((p = strstr(p, "python3.")) != NULL) {
        int minor = atoi(p + strlen("python3."));
        if (minor > 0)
            return minor;
        p += strlen("python3.");
    }
    return -1;
}

/*
 * Locate _PyRuntime in the target by scanning its file-backed mappings for the
 * ELF object that defines the symbol (the python binary for a static build, or
 * libpython for a shared build), then translate to a runtime address using that
 * object's load bias.
 */
static int
locate_runtime(struct pysym* py)
{
    char mapspath[64];
    snprintf(mapspath, sizeof(mapspath), "/proc/%d/maps", py->pid);
    FILE* f = fopen(mapspath, "r");
    if (f == NULL)
        return -1;

    char seen[64][512];
    int nseen = 0;
    char line[4096];
    int ok = 0;

    while (!ok && fgets(line, sizeof(line), f) != NULL) {
        unsigned long start, end, offset;
        char perms[8];
        int pathpos = 0;
        if (sscanf(line, "%lx-%lx %7s %lx %*x:%*x %*u %n", &start, &end, perms, &offset, &pathpos) < 4)
            continue;
        const char* path = line + pathpos;
        while (*path == ' ')
            path++;
        if (*path != '/')
            continue;
        char* nl = strchr(path, '\n');
        if (nl != NULL)
            *nl = '\0';

        int dup = 0;
        for (int i = 0; i < nseen; i++)
            if (strcmp(seen[i], path) == 0) {
                dup = 1;
                break;
            }
        if (dup)
            continue;
        if (nseen < 64)
            snprintf(seen[nseen++], 512, "%s", path);

        uint64_t vaddr = 0;
        int et = ET_NONE;
        if (!elf_find_symbol(path, "_PyRuntime", &vaddr, &et))
            continue;

        /* Found the defining object. Compute its load bias from the lowest
         * mapping of this same file (ELF header mapping, file offset 0). For a
         * fixed-load ET_EXEC the bias is 0 (st_value already absolute). */
        uint64_t bias = 0;
        if (et == ET_DYN) {
            FILE* g = fopen(mapspath, "r");
            if (g != NULL) {
                unsigned long best_start = 0;
                unsigned long best_off = 0;
                int have = 0;
                char l2[4096];
                while (fgets(l2, sizeof(l2), g) != NULL) {
                    unsigned long s2, e2, o2;
                    char pm[8];
                    int pp = 0;
                    if (sscanf(l2, "%lx-%lx %7s %lx %*x:%*x %*u %n", &s2, &e2, pm, &o2, &pp) < 4)
                        continue;
                    const char* p2 = l2 + pp;
                    while (*p2 == ' ')
                        p2++;
                    char* nl2 = strchr(p2, '\n');
                    if (nl2 != NULL)
                        *nl2 = '\0';
                    if (strcmp(p2, path) != 0)
                        continue;
                    if (!have || s2 < best_start) {
                        best_start = s2;
                        best_off = o2;
                        have = 1;
                    }
                }
                fclose(g);
                if (have)
                    bias = best_start - best_off;
            }
        }

        py->runtime = bias + vaddr;
        py->py_minor = parse_py_minor(path);
        ok = 1;
    }

    fclose(f);
    return ok ? 0 : -1;
}

/* ------------------------------------------------------------------ public */

struct pysym*
pysym_new(pid_t pid)
{
    if (elf_version(EV_CURRENT) == EV_NONE)
        return NULL;

    struct pysym* py = calloc(1, sizeof(*py));
    if (py == NULL)
        return NULL;
    py->pid = pid;
    py->py_minor = -1;

    if (locate_runtime(py) != 0) {
        free(py); /* not a CPython process we can read */
        return NULL;
    }

    py->supported = (py->py_minor == 12);
    if (!py->supported) {
        fprintf(stderr,
                "dd_offcpu: python frame walking supports 3.12 only; detected 3.%d, disabling\n",
                py->py_minor);
    }
    return py;
}

void
pysym_free(struct pysym* py)
{
    free(py);
}

/* Format one frame from its PyCodeObject into out. */
static int
format_code(const struct pysym* py, uint64_t code, char* out, size_t outlen)
{
    if (code == 0)
        return -1;

    char qual[192];
    if (read_pystr(py, read_ptr(py, code + PY312_CODE_QUALNAME), qual, sizeof(qual)) != 0 &&
        read_pystr(py, read_ptr(py, code + PY312_CODE_NAME), qual, sizeof(qual)) != 0)
        return -1;

    char file[160];
    if (read_pystr(py, read_ptr(py, code + PY312_CODE_FILENAME), file, sizeof(file)) != 0)
        file[0] = '\0';

    /* Internal interpreter trampoline shims (co_filename "<shim>") are not user
     * frames; drop them. */
    if (strcmp(file, "<shim>") == 0)
        return -1;

    int32_t firstline = 0;
    read_mem(py, code + PY312_CODE_FIRSTLINENO, &firstline, sizeof(firstline));

    const char* base = file;
    const char* slash = strrchr(file, '/');
    if (slash != NULL)
        base = slash + 1;

    if (base[0] != '\0')
        snprintf(out, outlen, "%s (%s:%d)", qual, base, firstline);
    else
        snprintf(out, outlen, "%s", qual);
    return 0;
}

int
pysym_walk(struct pysym* py, pid_t tid, char (*frames)[256], int max_frames)
{
    if (py == NULL || !py->supported || max_frames <= 0)
        return 0;

    /* Find the PyThreadState whose OS thread id matches. */
    uint64_t interp = read_ptr(py, py->runtime + PY312_RUNTIME_INTERP_HEAD);
    uint64_t tstate = 0;
    for (int gi = 0; interp != 0 && gi < 64 && tstate == 0; gi++) {
        uint64_t t = read_ptr(py, interp + PY312_INTERP_THREADS_HEAD);
        for (int ti = 0; t != 0 && ti < 100000; ti++) {
            uint64_t native = read_ptr(py, t + PY312_TSTATE_NATIVE_TID);
            if (native == (uint64_t)tid) {
                tstate = t;
                break;
            }
            t = read_ptr(py, t + PY312_TSTATE_NEXT);
        }
        interp = read_ptr(py, interp + PY312_INTERP_NEXT);
    }
    if (tstate == 0)
        return 0;

    uint64_t cframe = read_ptr(py, tstate + PY312_TSTATE_CFRAME);
    uint64_t frame = read_ptr(py, cframe + PY312_CFRAME_CURRENT_FRAME);

    int n = 0;
    for (int i = 0; frame != 0 && i < MAX_FRAME_WALK && n < max_frames; i++) {
        uint8_t owner = 0;
        read_mem(py, frame + PY312_FRAME_OWNER, &owner, sizeof(owner));
        uint64_t next = read_ptr(py, frame + PY312_FRAME_PREVIOUS);

        if (owner != FRAME_OWNED_BY_CSTACK) {
            uint64_t code = read_ptr(py, frame + PY312_FRAME_F_CODE);
            if (format_code(py, code, frames[n], sizeof(frames[n])) == 0)
                n++;
        }
        frame = next;
    }
    return n;
}
