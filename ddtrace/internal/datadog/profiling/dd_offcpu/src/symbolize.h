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
 * Resolve a single user-space address. Writes a NUL-terminated description into
 * buf: "func", "func+0xoff", "module+0xoff" (stripped object), or "0xaddr" when
 * the address is not in any executable file-backed mapping.
 *
 * Returns 0 when the address lies in a known executable mapping and -1
 * otherwise. A -1 usually means the kernel's frame-pointer unwinder walked off
 * into non-code memory (CPython and many release builds omit frame pointers),
 * so callers may use it to stop unwinding.
 */
int
symbolizer_resolve(struct symbolizer* s, uint64_t addr, char* buf, size_t buflen);

#endif /* DDOFFCPU_SYMBOLIZE_H */
