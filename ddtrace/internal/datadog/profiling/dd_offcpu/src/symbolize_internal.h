/*
 * Internal symbolizer helpers, exposed only so they can be unit tested. These
 * are not part of the public symbolizer API (see symbolize.h) and should not be
 * relied on by the daemon beyond symbolize.c itself.
 */
#ifndef DDOFFCPU_SYMBOLIZE_INTERNAL_H
#define DDOFFCPU_SYMBOLIZE_INTERNAL_H

#include <gelf.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Parse one /proc/<pid>/maps line. Returns 1 when the line describes an
 * executable, file-backed mapping with an absolute path: *start, *end and
 * *offset are filled and the NUL-terminated path is copied into
 * path[0..pathcap). Returns 0 otherwise (non-executable mapping, anonymous
 * mapping, pseudo-paths such as "[heap]"/"[stack]", or a malformed line).
 */
int
ddoffcpu_parse_maps_line(const char* line, uint64_t* start, uint64_t* end, uint64_t* offset, char* path,
                         size_t pathcap);

/*
 * Map a symbol's virtual address to a file offset via the PT_LOAD segment that
 * contains it. Returns UINT64_MAX if no segment matches.
 */
uint64_t
ddoffcpu_vaddr_to_foff(const GElf_Phdr* loads, size_t nloads, uint64_t vaddr);

#ifdef __cplusplus
}
#endif

#endif /* DDOFFCPU_SYMBOLIZE_INTERNAL_H */
