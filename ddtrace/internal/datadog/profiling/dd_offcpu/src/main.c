// SPDX-License-Identifier: Apache-2.0
/*
 * dd_offcpu daemon.
 *
 * Loads the off-CPU BPF program against a target process, polls the ring buffer
 * of completed off-CPU intervals, symbolizes native and Python frames, and emits
 * a gzipped pprof profile (a production build would export via libdatadog).
 *
 * Privilege: expects cap_bpf,cap_perfmon to be set on the binary at install
 * time (`setcap cap_bpf,cap_perfmon=ep`); runs as a plain subprocess otherwise.
 *
 * Usage: dd_offcpu --pid <pid> [--min-block-us <us>] [--output <file>]
 */
#include <argp.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "offcpu.h"
#include "offcpu.skel.h"
#include "pprof.h"
#include "pysym.h"
#include "symbolize.h"

static volatile sig_atomic_t exiting = 0;

struct env
{
    pid_t pid;
    unsigned long min_block_us;
    const char* output;
};

static struct env env = {
    .pid = 0,
    .min_block_us = 0,
    .output = "offcpu.pb.gz",
};

const char* argp_program_version = "dd_offcpu 0.0.1 (spike)";

static const struct argp_option options[] = {
    { "pid", 'p', "PID", 0, "Target process id to profile (required)", 0 },
    { "min-block-us", 'm', "US", 0, "Minimum off-CPU interval to report, microseconds", 0 },
    { "output", 'o', "FILE", 0, "pprof output path", 0 },
    { 0 },
};

static error_t
parse_arg(int key, char* arg, struct argp_state* state)
{
    switch (key) {
        case 'p':
            env.pid = atoi(arg);
            break;
        case 'm':
            env.min_block_us = strtoul(arg, NULL, 10);
            break;
        case 'o':
            env.output = arg;
            break;
        case ARGP_KEY_END:
            if (env.pid <= 0)
                argp_error(state, "--pid is required");
            break;
        default:
            return ARGP_ERR_UNKNOWN;
    }
    return 0;
}

static const struct argp argp = {
    .options = options,
    .parser = parse_arg,
    .doc = "Measure off-CPU time for a Python process via eBPF.",
};

static int
libbpf_print_fn(enum libbpf_print_level level, const char* format, va_list args)
{
    if (level == LIBBPF_DEBUG)
        return 0;
    return vfprintf(stderr, format, args);
}

/* Inode of a process's PID namespace. The kernel exposes it as the inode of the
 * magic symlink /proc/<pid>/ns/pid (an nsfs node); stat() follows the symlink
 * and returns that inode in st_ino. This matches pid_namespace->ns.inum that
 * the BPF program reads, so the two can be compared across the namespace
 * boundary. Returns 0 if it cannot be determined (e.g. permission denied), in
 * which case the BPF side falls back to matching on pid alone. */
static unsigned int
pid_namespace_ino(pid_t pid)
{
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/ns/pid", pid);

    struct stat st;
    if (stat(path, &st) != 0) {
        fprintf(stderr, "dd_offcpu: cannot stat %s (%s); matching on pid only\n", path, strerror(errno));
        return 0;
    }
    return (unsigned int)st.st_ino;
}

static void
sig_handler(int sig)
{
    (void)sig;
    exiting = 1;
}

struct daemon_ctx
{
    struct symbolizer* sym;
    struct pysym* py;
    struct pprof* prof;
    int stackmap_fd;
};

/* Resolve the native user-space frames for one event into `out` (leaf-first).
 * The BPF program stored the stack (captured when the thread went off-CPU) in
 * the stack-trace map under e->user_stack_id; we read the address array back and
 * symbolize each frame, stopping at the first address outside executable code
 * (without frame pointers the kernel unwinder produces garbage past the leaf).
 * Returns the number of frames written. */
static int
collect_native_stack(struct daemon_ctx* dctx, const struct offcpu_event* e, char (*out)[256], int max)
{
    if (e->user_stack_id < 0 || dctx->stackmap_fd < 0)
        return 0;

    __u64 ips[MAX_STACK_DEPTH];
    __u32 key = (__u32)e->user_stack_id;
    if (bpf_map_lookup_elem(dctx->stackmap_fd, &key, ips) != 0)
        return 0;

    int n = 0;
    for (int i = 0; i < MAX_STACK_DEPTH && ips[i] != 0 && n < max; i++) {
        if (symbolizer_resolve(dctx->sym, ips[i], out[n], sizeof(out[n])) != 0)
            break;
        n++;
    }
    return n;
}

static int
handle_event(void* ctx, void* data, size_t size)
{
    struct daemon_ctx* dctx = ctx;
    const struct offcpu_event* e = data;

    if (size < sizeof(*e))
        return 0;

    /* Milestone 2: TID + off-CPU duration. */
    printf("tid=%u comm=%s off_cpu=%.3f ms\n", e->tid, e->comm, e->delta_ns / 1e6);

    /* Milestone 4: Python frames (leaf-first) for the blocked thread. */
    char py_frames[MAX_STACK_DEPTH][256];
    int npy = pysym_walk(dctx->py, (pid_t)e->tid, py_frames, MAX_STACK_DEPTH);
    for (int i = 0; i < npy; i++)
        printf("    py: %s\n", py_frames[i]);

    /* Milestone 3: native frames (leaf-first) resolved from the user stack id. */
    char nat_frames[MAX_STACK_DEPTH][256];
    int nnat = collect_native_stack(dctx, e, nat_frames, MAX_STACK_DEPTH);
    for (int i = 0; i < nnat; i++)
        printf("    native: %s\n", nat_frames[i]);

    /* Milestone 5: accumulate into the pprof profile. The innermost frame is the
     * native syscall the thread blocked in, so native frames lead, then the
     * Python frames from innermost outward. */
    uint64_t locs[2 * MAX_STACK_DEPTH];
    int nloc = 0;
    for (int i = 0; i < nnat && nloc < (int)(sizeof(locs) / sizeof(locs[0])); i++)
        locs[nloc++] = pprof_intern_frame(dctx->prof, nat_frames[i], "", 0);
    for (int i = 0; i < npy && nloc < (int)(sizeof(locs) / sizeof(locs[0])); i++)
        locs[nloc++] = pprof_intern_frame(dctx->prof, py_frames[i], "", 0);
    if (nloc == 0)
        locs[nloc++] = pprof_intern_frame(dctx->prof, "[unresolved]", "", 0);
    pprof_add_sample(dctx->prof, locs, nloc, (int64_t)e->delta_ns, e->tid, e->comm);
    return 0;
}

int
main(int argc, char** argv)
{
    int err = argp_parse(&argp, argc, argv, 0, NULL, NULL);
    if (err != 0)
        return err;

    libbpf_set_print(libbpf_print_fn);
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);

    struct offcpu_bpf* skel = offcpu_bpf__open();
    if (skel == NULL) {
        fprintf(stderr, "failed to open BPF skeleton\n");
        return 1;
    }

    unsigned int pid_ns_ino = pid_namespace_ino(env.pid);
    skel->rodata->targ_pid = (unsigned)env.pid;
    skel->rodata->targ_pid_ns_ino = pid_ns_ino;
    skel->rodata->min_block_ns = (unsigned long long)env.min_block_us * 1000ULL;

    err = offcpu_bpf__load(skel);
    if (err != 0) {
        fprintf(stderr, "failed to load BPF skeleton: %d\n", err);
        goto cleanup_skel;
    }

    err = offcpu_bpf__attach(skel);
    if (err != 0) {
        fprintf(stderr, "failed to attach BPF programs: %d\n", err);
        goto cleanup_skel;
    }

    struct daemon_ctx dctx = {
        .sym = symbolizer_new(env.pid),
        .py = pysym_new(env.pid),
        .prof = pprof_new("off-cpu", "nanoseconds"),
        .stackmap_fd = bpf_map__fd(skel->maps.stackmap),
    };
    if (dctx.prof == NULL) {
        fprintf(stderr, "failed to allocate pprof profile\n");
        err = 1;
        goto cleanup_ctx;
    }

    struct ring_buffer* rb = ring_buffer__new(bpf_map__fd(skel->maps.events), handle_event, &dctx, NULL);
    if (rb == NULL) {
        fprintf(stderr, "failed to create ring buffer\n");
        err = 1;
        goto cleanup_ctx;
    }

    fprintf(stderr, "dd_offcpu: profiling pid %d (pid ns inode %u) (ctrl-c to stop)\n", env.pid, pid_ns_ino);
    while (!exiting) {
        err = ring_buffer__poll(rb, 100 /* ms */);
        if (err == -EINTR) {
            err = 0;
            break;
        }
        if (err < 0) {
            fprintf(stderr, "ring buffer poll failed: %d\n", err);
            break;
        }
    }

    ring_buffer__free(rb);

    if (pprof_write(dctx.prof, env.output) == 0)
        fprintf(stderr, "dd_offcpu: wrote %s\n", env.output);
    else
        fprintf(stderr, "dd_offcpu: failed to write %s\n", env.output);

cleanup_ctx:
    pprof_free(dctx.prof);
    pysym_free(dctx.py);
    symbolizer_free(dctx.sym);
cleanup_skel:
    offcpu_bpf__destroy(skel);
    return err != 0 ? 1 : 0;
}
