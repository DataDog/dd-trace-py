#include "symbolize.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Spike scaffold. The full implementation parses /proc/<pid>/maps to build a
 * list of mapped ELF objects, then resolves an address by locating the owning
 * mapping, converting to a file offset, and binary-searching that object's
 * .symtab / .dynsym (via libelf). For now the structure is in place and
 * resolution is a stub so the daemon links and runs end to end.
 */

struct symbolizer
{
    pid_t pid;
    /* TODO: parsed /proc/<pid>/maps entries + per-object symbol tables. */
};

struct symbolizer*
symbolizer_new(pid_t pid)
{
    struct symbolizer* s = calloc(1, sizeof(*s));
    if (s == NULL)
        return NULL;
    s->pid = pid;
    return s;
}

void
symbolizer_free(struct symbolizer* s)
{
    free(s);
}

int
symbolizer_resolve(struct symbolizer* s, uint64_t addr, char* buf, size_t buflen)
{
    (void)s;
    /* TODO: map addr -> ELF object -> nearest symbol. */
    snprintf(buf, buflen, "0x%llx", (unsigned long long)addr);
    return -1;
}
