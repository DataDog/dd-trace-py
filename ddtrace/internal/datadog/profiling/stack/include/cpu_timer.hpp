#pragma once

#include <cstdint>

#include "echion/timing.h"

class EchionSampler;
typedef struct _ts PyThreadState;

namespace Datadog {
namespace CpuTimer {

struct DebugStats
{
    bool supported = false;
    bool configured = false;
    bool active = false;
    bool permanently_disabled = false;
    bool replacing_wall_cpu = false;
    uint64_t interval_ms = 0;
    uint64_t live_count = 0;
    uint64_t retired_count = 0;
    uint64_t leaked_altstack_count = 0;
    uint64_t pending_unprepared = 0;
    uint64_t app_altstack_present = 0;
    uint64_t reused_altstack_count = 0;
    uint64_t reused_altstack_too_small_count = 0;
    uint64_t blocked_signal_count = 0;
    uint64_t tid_out_of_bounds = 0;
    uint64_t timer_syscall_failures = 0;
    uint64_t accepted_signal_oob_tid_count = 0;
    uint64_t handler_hijack_disable_count = 0;
    uint64_t fast_copy_conflict_count = 0;
    uint64_t dropped_count = 0;
    uint64_t dropped_cpu_ns = 0;
    uint64_t capture_failed_count = 0;
    uint64_t capture_failed_cpu_ns = 0;
    uint64_t residual_cpu_ns = 0;
    uint64_t stage2_invalid_frame_count = 0;
};

class Engine
{
  public:
    static Engine& get();

    void configure(bool enabled, uint64_t interval_ms);
    bool start();
    void shutdown(EchionSampler& echion);
    void postfork_child();
    void disable_for_fault_handler_swap();

    void register_thread(uint64_t python_thread_id, uint64_t native_id, const char* name, PyThreadState* tstate);
    void unregister_thread(uint64_t python_thread_id);
    void drain(EchionSampler& echion);

    bool replaces_wall_sampler_cpu_time() const;
    bool configured_enabled() const;
    microsecond_t interval_us() const;

    DebugStats debug_stats() const;
    void debug_set_fault_injection(bool enabled);
};

} // namespace CpuTimer
} // namespace Datadog
