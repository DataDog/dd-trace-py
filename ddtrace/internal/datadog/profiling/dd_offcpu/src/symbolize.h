/*
 * Native ELF symbolizer.
 *
 * Resolves user-space instruction pointers (captured by the BPF stack walker)
 * to function names by reading /proc/<pid>/maps and the symbol tables of the
 * mapped ELF objects.
 *
 * Spike milestone 3: native function names in output.
 */
#ifndef DDOFFCPU_SYMBOLIZE_H
#define DDOFFCPU_SYMBOLIZE_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

struct symbolizer;

/* Create a symbolizer bound to a process. Returns NULL on failure. */
struct symbolizer*
symbolizer_new(pid_t pid);

void
symbolizer_free(struct symbolizer* s);

/*
 * Resolve a single user-space address to a function name. Writes a
 * NUL-terminated name into buf and returns 0 on success, -1 if the address
 * could not be resolved.
 */
int
symbolizer_resolve(struct symbolizer* s, uint64_t addr, char* buf, size_t buflen);

#endif /* DDOFFCPU_SYMBOLIZE_H */
