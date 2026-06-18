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

/* Set by the loader (offcpu_bpf__open) before load. The target process whose
 * threads we measure; 0 would mean "all", which the spike does not use. */
const volatile __u32 targ_tgid = 0;

/* Minimum off-CPU duration to report, in nanoseconds. Set by the loader to
 * drop sub-threshold noise in the kernel before it reaches userspace. */
const volatile __u64 min_block_ns = 0;

/* tid -> timestamp (ns) when that thread left the CPU. */
struct
{
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, __u32);
    __type(value, __u64);
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

static __always_inline bool
is_target(__u32 tgid)
{
    return targ_tgid != 0 && tgid == targ_tgid;
}

/* Stamp the moment a thread leaves the CPU. */
static __always_inline void
record_off_cpu(struct task_struct* t)
{
    __u32 tgid = BPF_CORE_READ(t, tgid);
    if (!is_target(tgid))
        return;

    __u32 tid = BPF_CORE_READ(t, pid);
    __u64 ts = bpf_ktime_get_ns();
    bpf_map_update_elem(&start, &tid, &ts, BPF_ANY);
}

/* Emit a completed interval when a thread returns to the CPU. */
static __always_inline void
record_on_cpu(void* ctx, struct task_struct* t)
{
    __u32 tgid = BPF_CORE_READ(t, tgid);
    if (!is_target(tgid))
        return;

    __u32 tid = BPF_CORE_READ(t, pid);
    __u64* tsp = bpf_map_lookup_elem(&start, &tid);
    if (!tsp)
        return;

    __u64 delta = bpf_ktime_get_ns() - *tsp;
    bpf_map_delete_elem(&start, &tid);

    if (delta < min_block_ns)
        return;

    struct offcpu_event* e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e)
        return;

    e->pid = tgid;
    e->tid = tid;
    e->delta_ns = delta;
    e->user_stack_id = bpf_get_stackid(ctx, &stackmap, BPF_F_USER_STACK);
    e->kern_stack_id = bpf_get_stackid(ctx, &stackmap, 0);
    bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), BPF_CORE_READ(t, comm));

    bpf_ringbuf_submit(e, 0);
}

SEC("tp_btf/sched_switch")
int
BPF_PROG(sched_switch, bool preempt, struct task_struct* prev, struct task_struct* next)
{
    record_off_cpu(prev);
    record_on_cpu(ctx, next);
    return 0;
}
