/*
 * Shared definitions for the dd_offcpu BPF program and the userspace daemon.
 *
 * This header is included on both sides of the BPF/userspace boundary, so it
 * must only use fixed-width integer types and contain no function definitions.
 * On the BPF side the types come from vmlinux.h; on the userspace side they
 * come from <linux/types.h>.
 */
#ifndef DDOFFCPU_OFFCPU_H
#define DDOFFCPU_OFFCPU_H

#define TASK_COMM_LEN 16

/* Maximum stack depth captured per off-CPU event. Matches the value used to
 * size the BPF_MAP_TYPE_STACK_TRACE value (PERF_MAX_STACK_DEPTH). */
#define MAX_STACK_DEPTH 127

/* A completed off-CPU interval, pushed to the ring buffer when a thread is
 * scheduled back onto a CPU. One event per off-CPU interval for the target
 * process. */
struct offcpu_event
{
    __u32 pid;                /* thread-group id (the target process)   */
    __u32 tid;                /* thread id that went off-CPU            */
    __u64 delta_ns;           /* off-CPU duration in nanoseconds        */
    __s32 user_stack_id;      /* key into stackmap, or < 0 on failure   */
    __s32 kern_stack_id;      /* key into stackmap, or < 0 on failure   */
    char comm[TASK_COMM_LEN]; /* thread comm at switch time             */
};

#endif /* DDOFFCPU_OFFCPU_H */
