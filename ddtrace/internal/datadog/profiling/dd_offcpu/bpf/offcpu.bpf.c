// SPDX-License-Identifier: GPL-2.0
/*
 * dd_offcpu BPF off-CPU program.
 *
 * Records how long each thread of the target process spends off-CPU. The
 * accumulation happens in the kernel: on every scheduler switch we stamp the
 * time the outgoing thread left the CPU, and when a thread is scheduled back in
 * we compute the delta and push a single completed event to a ring buffer. This
 * keeps the userspace daemon from having to observe every sched_switch.
 *
 * Hook: tp_btf/sched_switch (BTF-enabled raw tracepoint, CO-RE). Minimum kernel
 * 5.8: BPF_MAP_TYPE_RINGBUF (5.8) is the binding constraint; tp_btf needs 5.5
 * and kernel BTF/CO-RE needs 5.4. Gate at runtime via feature probe, not version.
 *
 * Reference: libbpf-tools offcputime.bpf.c, FHP support/ebpf/off_cpu.ebpf.c.
 */
#include "vmlinux.h"

#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

#include "offcpu.h"

char LICENSE[] SEC("license") = "GPL";

/* Set by the loader (offcpu_bpf__open) before load.
 *
 * targ_pid is the target process id *as seen inside the target's own PID
 * namespace* — i.e. the value the target gets from getpid(). The daemon and the
 * target share a PID namespace (the sidecar is spawned by the instrumented
 * app), so this is just the --pid passed on the command line.
 *
 * targ_pid_ns_ino disambiguates that pid across namespaces: two containers can
 * both host pid 1234, but their PID namespaces have distinct inode numbers. The
 * loader reads it from stat("/proc/<pid>/ns/pid"). 0 disables the inode check
 * (match on pid alone) for hosts/kernels where it cannot be determined. */
const volatile __u32 targ_pid = 0;
const volatile __u32 targ_pid_ns_ino = 0;

/* Minimum off-CPU duration to report, in nanoseconds. Set by the loader to
 * drop sub-threshold noise in the kernel before it reaches userspace. */
const volatile __u64 min_block_ns = 0;

/* Per-thread state captured at the moment a thread leaves the CPU. The stack
 * must be sampled here (in the sched_switch handler `current` is still `prev`,
 * the outgoing thread) so it reflects where the thread blocked, not where it
 * later resumes. It is carried until the thread is scheduled back in. */
struct offcpu_start
{
    __u64 ts;
    __s32 user_stack_id;
    __s32 kern_stack_id;
};

/* global tid -> off-CPU start state. */
struct
{
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, __u32);
    __type(value, struct offcpu_start);
} start SEC(".maps");

/* User and kernel stack traces, keyed by stack id stamped into each event. */
struct
{
    __uint(type, BPF_MAP_TYPE_STACK_TRACE);
    __uint(max_entries, 16384);
    __uint(key_size, sizeof(__u32));
    __uint(value_size, MAX_STACK_DEPTH * sizeof(__u64));
} stackmap SEC(".maps");

/* Completed off-CPU intervals consumed by the userspace daemon. */
struct
{
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} events SEC(".maps");

/* Upper bound on PID-namespace nesting we will walk. The kernel caps nesting at
 * MAX_PID_NS_LEVEL (32); bounding the unrolled loop keeps the verifier happy. */
#define MAX_PIDNS_LEVEL 32

/* Resolve a struct pid to the (nr, namespace inode) pair at its *deepest*
 * level — i.e. the id as seen from inside the namespace the task actually lives
 * in. numbers[0] is the init namespace (global); numbers[pid->level] is the
 * innermost (container) namespace. The daemon's --pid and ns inode are taken in
 * that same innermost namespace, so this is what we compare against. */
static __always_inline void
pid_ns_local(struct pid* pid, __u32* nr, __u32* ns_ino)
{
    *nr = 0;
    *ns_ino = 0;
    if (pid == NULL)
        return;

    unsigned int level = BPF_CORE_READ(pid, level);

    /* We want numbers[level]. A direct runtime-index access compiles, but CO-RE
     * only relocates the *field* offsets (offsetof(numbers), offsetof(nr)) — the
     * array *stride* is baked in as the build kernel's sizeof(struct upid), so it
     * would read the wrong element if that struct's layout ever differs on the
     * target. Unrolling with a constant index instead emits one byte-offset
     * relocation for the whole path, which the loader recomputes (stride
     * included) against the target BTF. `level` is fixed, so exactly one
     * iteration matches; break once it does. */
#pragma unroll
    for (int i = 0; i < MAX_PIDNS_LEVEL; i++) {
        if (i == level) {
            *nr = BPF_CORE_READ(pid, numbers[i].nr);
            *ns_ino = BPF_CORE_READ(pid, numbers[i].ns, ns.inum);
            break;
        }
    }
}

/* True if task t belongs to the target process, compared in the target's PID
 * namespace (pid + ns inode), not the global init namespace. */
static __always_inline bool
is_target(struct task_struct* t)
{
    if (targ_pid == 0)
        return false;

    struct pid* p = BPF_CORE_READ(t, signal, pids[PIDTYPE_TGID]);
    __u32 nr, ns_ino;
    pid_ns_local(p, &nr, &ns_ino);

    if (nr != targ_pid)
        return false;
    /* ns inode 0 => loader could not determine it; fall back to pid-only. */
    return targ_pid_ns_ino == 0 || ns_ino == targ_pid_ns_ino;
}

/* Thread id of t as seen inside its own PID namespace. The daemon resolves
 * Python frames through the container's /proc, so it needs the namespace-local
 * tid, not the global one. */
static __always_inline __u32
task_ns_tid(struct task_struct* t)
{
    __u32 nr, ns_ino;
    pid_ns_local(BPF_CORE_READ(t, thread_pid), &nr, &ns_ino);
    return nr;
}

/* Stamp the moment a thread leaves the CPU, capturing the stack of where it
 * blocked. Keyed by the global tid (unique and cheap); the namespace-local id
 * is only needed when we emit the event. `ctx` is the sched_switch context and
 * `t` is `prev` (== current), so the captured stacks belong to this thread. */
static __always_inline void
record_off_cpu(void* ctx, struct task_struct* t)
{
    if (!is_target(t))
        return;

    __u32 gtid = BPF_CORE_READ(t, pid);
    struct offcpu_start s = {
        .ts = bpf_ktime_get_ns(),
        .user_stack_id = bpf_get_stackid(ctx, &stackmap, BPF_F_USER_STACK),
        .kern_stack_id = bpf_get_stackid(ctx, &stackmap, 0),
    };
    bpf_map_update_elem(&start, &gtid, &s, BPF_ANY);
}

/* Emit a completed interval when a thread returns to the CPU, using the stacks
 * captured when it went off-CPU. */
static __always_inline void
record_on_cpu(void* ctx, struct task_struct* t)
{
    (void)ctx;
    if (!is_target(t))
        return;

    __u32 gtid = BPF_CORE_READ(t, pid);
    struct offcpu_start* s = bpf_map_lookup_elem(&start, &gtid);
    if (!s)
        return;

    __u64 delta = bpf_ktime_get_ns() - s->ts;
    __s32 user_stack_id = s->user_stack_id;
    __s32 kern_stack_id = s->kern_stack_id;
    bpf_map_delete_elem(&start, &gtid);

    if (delta < min_block_ns)
        return;

    struct offcpu_event* e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e)
        return;

    e->pid = targ_pid;
    e->tid = task_ns_tid(t);
    e->delta_ns = delta;
    e->user_stack_id = user_stack_id;
    e->kern_stack_id = kern_stack_id;
    bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), BPF_CORE_READ(t, comm));

    bpf_ringbuf_submit(e, 0);
}

SEC("tp_btf/sched_switch")
int
BPF_PROG(sched_switch, bool preempt, struct task_struct* prev, struct task_struct* next)
{
    record_off_cpu(ctx, prev);
    record_on_cpu(ctx, next);
    return 0;
}
