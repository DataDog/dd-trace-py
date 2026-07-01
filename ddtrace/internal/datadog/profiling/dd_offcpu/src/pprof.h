/*
 * Minimal pprof (github.com/google/pprof) profile writer.
 *
 * Emits a gzipped profile.proto that `go tool pprof` accepts directly. This is
 * a deliberately small, dependency-free encoder for the spike — enough to hit
 * milestone 5 (flame graph). A production build would emit through libdatadog's
 * profile exporter instead (for upload, auth, and standardized labels).
 *
 * Spike milestone 5: off-CPU flame graph via `go tool pprof`.
 */
#ifndef DDOFFCPU_PPROF_H
#define DDOFFCPU_PPROF_H

#include <stddef.h>
#include <stdint.h>

struct pprof;

/* Create a profile with a single sample type, e.g. ("off-cpu", "nanoseconds"). */
struct pprof*
pprof_new(const char* sample_type, const char* unit);

void
pprof_free(struct pprof* p);

/*
 * Intern one stack frame and return its pprof location id (reused across
 * samples). `file` may be "" and `line` 0 when unknown.
 */
uint64_t
pprof_intern_frame(struct pprof* p, const char* name, const char* file, int line);

/*
 * Add one sample. `locs` lists location ids leaf-first (locs[0] is the
 * innermost frame). `value` is the metric (off-CPU nanoseconds). `tid`/`comm`
 * are attached as labels.
 */
void
pprof_add_sample(struct pprof* p, const uint64_t* locs, int nlocs, int64_t value, uint32_t tid, const char* comm);

/* Serialize, gzip, and write to path. Returns 0 on success. */
int
pprof_write(struct pprof* p, const char* path);

#endif /* DDOFFCPU_PPROF_H */
