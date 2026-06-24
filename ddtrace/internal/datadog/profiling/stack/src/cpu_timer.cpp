#include "cpu_timer.hpp"

#include "cpu_sample_ring.hpp"

#include "echion/danger.h"
#include "echion/frame.h"
#include "echion/stacks.h"
#include "echion/threads.h"
#include "echion/vm.h"

#include "sampler.hpp"
#include "stack_renderer.hpp"

#include "echion/echion_sampler.h"

#include "dd_wrapper/include/profiler_state.hpp"
#include "profiling_helpers/frame_accessors.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <csetjmp>
#include <csignal>
#include <cstddef>
#include <cstdlib>
#include <fstream>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#if defined(__linux__)
#include <pthread.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#ifndef SIGEV_THREAD_ID
#define SIGEV_THREAD_ID 4
#endif
#ifndef sigev_notify_thread_id
#define sigev_notify_thread_id _sigev_un._tid
#endif
#endif

namespace Datadog {
namespace CpuTimer {

namespace {

#if defined(__linux__) && PY_VERSION_HEX >= 0x030e0000 && !defined(Py_GIL_DISABLED)
#define DD_CPU_TIMER_SUPPORTED 1
#else
#define DD_CPU_TIMER_SUPPORTED 0
#endif

#if DD_CPU_TIMER_SUPPORTED

using kernel_timer_id = int;

static_assert(std::atomic<bool>::is_always_lock_free, "CPU timer handler requires lock-free bool atomics");
static_assert(std::atomic<uint64_t>::is_always_lock_free, "CPU timer handler requires lock-free uint64 atomics");
static_assert(std::atomic<uint32_t>::is_always_lock_free, "CPU timer handler requires lock-free uint32 atomics");

constexpr uint64_t kDefaultIntervalMs = 10;
// AIDEV-NOTE: Keep the effective minimum above 1ms. Ecosystem stress with
// AnyIO/httpcore on Trio showed that 1ms SIGPROF delivery can livelock
// condition/event-loop handoff paths even with SA_RESTART. The setting remains
// private, so clamp aggressively for stability while keeping the 10ms default.
constexpr uint64_t kMinIntervalMs = 2;
constexpr uint32_t kDefaultRingCapacity = 64;
constexpr size_t kMinAltStackSize = 128 * 1024;
constexpr size_t kAltStackSigstkszMultiplier = 4;
constexpr size_t kMaxSlotBytes = 256 * 1024 * 1024;

int g_cookie;

pid_t
raw_gettid()
{
    return static_cast<pid_t>(syscall(SYS_gettid));
}

clockid_t
thread_cpu_clock(pid_t tid)
{
    constexpr unsigned long CPUCLOCK_SCHED = 2;
    constexpr unsigned long CPUCLOCK_PERTHREAD_MASK = 4;
    const unsigned long encoded = ((~static_cast<unsigned long>(tid)) << 3) |
                                  (CPUCLOCK_SCHED | CPUCLOCK_PERTHREAD_MASK);
    return static_cast<clockid_t>(encoded);
}

uint64_t
clock_to_ns(clockid_t clock_id)
{
    struct timespec ts
    {};
    if (clock_gettime(clock_id, &ts) != 0) {
        return 0;
    }
    return static_cast<uint64_t>(ts.tv_sec) * 1'000'000'000ULL + static_cast<uint64_t>(ts.tv_nsec);
}

uint64_t
thread_cpu_time_ns()
{
    return clock_to_ns(CLOCK_THREAD_CPUTIME_ID);
}

uint64_t
thread_cpu_time_ns(pid_t tid)
{
    return clock_to_ns(thread_cpu_clock(tid));
}

bool
set_sigev_thread_id(struct sigevent& sev, pid_t tid)
{
#ifdef sigev_notify_thread_id
    sev.sigev_notify_thread_id = tid;
    return true;
#else
    (void)sev;
    (void)tid;
    return false;
#endif
}

long
read_pid_max()
{
    std::ifstream f("/proc/sys/kernel/pid_max");
    long value = 0;
    if (f >> value && value > 0) {
        return value;
    }
    return 4'194'304;
}

bool
is_signal_blocked(int signo)
{
    sigset_t mask;
    if (pthread_sigmask(SIG_BLOCK, nullptr, &mask) != 0) {
        return true;
    }
    return sigismember(&mask, signo) == 1;
}

size_t
page_size()
{
    static const size_t value = [] {
        long v = sysconf(_SC_PAGESIZE);
        return v > 0 ? static_cast<size_t>(v) : static_cast<size_t>(4096);
    }();
    return value;
}

size_t
usable_alt_stack_size()
{
    return std::max(kMinAltStackSize, kAltStackSigstkszMultiplier * static_cast<size_t>(SIGSTKSZ));
}

struct OwnedAltStack
{
    void* mapping = nullptr;
    size_t mapping_size = 0;
    void* stack_sp = nullptr;
    size_t stack_size = 0;
    stack_t previous{};
    bool installed = false;
    bool detached = false;
    bool using_existing = false;
};

struct CaptureState
{
    uint64_t python_thread_id = 0;
    uint64_t native_tid = 0;
    std::string name;
    PyThreadState* tstate = nullptr;
    kernel_timer_id timer_id = -1;
    uint64_t last_cpu_ns = 0;
    OwnedAltStack altstack;
    CpuSampleRing ring;
    sigjmp_buf fault_env;
    volatile sig_atomic_t fault_armed = 0;
    bool timer_deleted = false;
    bool retired = false;

    std::atomic<uint64_t> dropped_count{ 0 };
    std::atomic<uint64_t> dropped_cpu_ns{ 0 };
    std::atomic<uint64_t> capture_failed_count{ 0 };
    std::atomic<uint64_t> capture_failed_cpu_ns{ 0 };
    std::atomic<uint64_t> residual_cpu_ns{ 0 };

    CaptureState(uint64_t thread_id, uint64_t tid, const char* thread_name)
      : python_thread_id(thread_id)
      , native_tid(tid)
      , name(thread_name == nullptr ? "" : thread_name)
      , ring(kDefaultRingCapacity)
    {
    }
};

static_assert(std::atomic<CaptureState*>::is_always_lock_free,
              "CPU timer handler requires lock-free capture-state pointer atomics");

struct EngineState
{
    std::atomic<bool> configured{ false };
    std::atomic<bool> active{ false };
    std::atomic<bool> started_once{ false };
    std::atomic<bool> stopped_once{ false };
    std::atomic<bool> permanently_disabled{ false };
    std::atomic<bool> replacing_wall_cpu{ false };
    std::atomic<bool> fault_injection_enabled{ false };
    std::atomic<uint64_t> interval_ms{ kDefaultIntervalMs };

    std::mutex registry_lock;
    std::unordered_map<uint64_t, std::unique_ptr<CaptureState>> live_by_thread_id;
    std::vector<std::unique_ptr<CaptureState>> retired;
    std::vector<std::pair<void*, size_t>> leaked_altstacks;

    std::unique_ptr<std::atomic<CaptureState*>[]> slots;
    std::unique_ptr<std::atomic<bool>[]> handler_active;
    size_t slot_capacity = 0;

    struct sigaction previous_sigprof{};
    bool has_previous_sigprof = false;
    bool exit_handler_registered = false;

    std::atomic<uint64_t> pending_unprepared{ 0 };
    std::atomic<uint64_t> app_altstack_present{ 0 };
    std::atomic<uint64_t> reused_altstack_count{ 0 };
    std::atomic<uint64_t> reused_altstack_too_small_count{ 0 };
    std::atomic<uint64_t> blocked_signal_count{ 0 };
    std::atomic<uint64_t> tid_out_of_bounds{ 0 };
    std::atomic<uint64_t> timer_syscall_failures{ 0 };
    std::atomic<uint64_t> accepted_signal_oob_tid_count{ 0 };
    std::atomic<uint64_t> handler_hijack_disable_count{ 0 };
    std::atomic<uint64_t> fast_copy_conflict_count{ 0 };
    std::atomic<uint64_t> stage2_invalid_frame_count{ 0 };
};

// AIDEV-NOTE: Intentionally leak EngineState. In embedders such as uWSGI with --skip-atexit,
// process shutdown can bypass Python profiler cleanup while per-thread CPU timers are still armed.
// A late SIGPROF may then interrupt C/C++ exit destructors. If EngineState were destructed, the
// handler could touch freed slot/handler_active storage. Leaking keeps the signal-handler control
// plane valid until the kernel tears the process down.
EngineState& g_state = *new EngineState();

void cpu_timer_signal_handler(int signo, siginfo_t* si, void* ucontext);
bool cpu_timer_fault_recover(int signo, siginfo_t* si, void* ucontext);

void
cpu_timer_process_exit()
{
    // Process-exit fallback for embedders that skip Python atexit and therefore bypass Engine::shutdown.
    // Keep this lock-free and allocation-free: late SIGPROF delivery should either be ignored by the kernel or
    // dropped by cpu_timer_signal_handler before touching per-thread CPython state.
    const bool was_active = g_state.active.exchange(false, std::memory_order_acq_rel);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    if (!was_active) {
        return;
    }

    struct sigaction ignore_action{};
    ignore_action.sa_handler = SIG_IGN;
    sigemptyset(&ignore_action.sa_mask);
    ignore_action.sa_flags = 0;
    (void)sigaction(SIGPROF, &ignore_action, nullptr);
}

void
forward_sigaction(const struct sigaction& previous, int signo, siginfo_t* si, void* ucontext, bool drop_default)
{
    if ((previous.sa_flags & SA_SIGINFO) != 0 && previous.sa_sigaction != nullptr) {
        previous.sa_sigaction(signo, si, ucontext);
        return;
    }

    if (previous.sa_handler == SIG_IGN || previous.sa_handler == nullptr) {
        return;
    }

    if (previous.sa_handler == SIG_DFL) {
        if (drop_default) {
            return;
        }
        sigaction(signo, &previous, nullptr);
        pthread_kill(pthread_self(), signo);
        return;
    }

    previous.sa_handler(signo);
}

void
forward_foreign_sigprof(int signo, siginfo_t* si, void* ucontext)
{
    if (!g_state.has_previous_sigprof) {
        return;
    }

    forward_sigaction(g_state.previous_sigprof, signo, si, ucontext, true);
}

bool
install_sigprof_handler()
{
    struct sigaction sa{};
    sa.sa_sigaction = cpu_timer_signal_handler;
    sigemptyset(&sa.sa_mask);
    sigaddset(&sa.sa_mask, SIGPROF);
    sa.sa_flags = SA_SIGINFO | SA_RESTART | SA_ONSTACK;

    struct sigaction previous{};
    if (sigaction(SIGPROF, &sa, &previous) != 0) {
        return false;
    }

    g_state.previous_sigprof = previous;
    g_state.has_previous_sigprof = true;
    return true;
}

bool
sigprof_handler_still_installed()
{
    struct sigaction current{};
    if (sigaction(SIGPROF, nullptr, &current) != 0) {
        return false;
    }
    return (current.sa_flags & SA_SIGINFO) != 0 && current.sa_sigaction == cpu_timer_signal_handler;
}

bool
fault_handlers_still_installed()
{
    return profiling_fault_handler_still_installed();
}

void
disable_current_thread_altstack(OwnedAltStack& altstack)
{
    if (!altstack.installed) {
        return;
    }
    if (altstack.using_existing) {
        altstack.installed = false;
        return;
    }
    if (altstack.mapping == nullptr || altstack.detached) {
        return;
    }

    stack_t current{};
    if (sigaltstack(nullptr, &current) != 0) {
        altstack.detached = true;
        g_state.leaked_altstacks.emplace_back(altstack.mapping, altstack.mapping_size);
        return;
    }

    const bool current_is_ours = !(current.ss_flags & SS_DISABLE) && current.ss_sp == altstack.stack_sp &&
                                 current.ss_size == altstack.stack_size;
    if (current_is_ours) {
        stack_t disable{};
        disable.ss_flags = SS_DISABLE;
        (void)sigaltstack(&disable, nullptr);
        (void)sigaltstack(&altstack.previous, nullptr);
        munmap(altstack.mapping, altstack.mapping_size);
        altstack.mapping = nullptr;
        altstack.installed = false;
        return;
    }

    munmap(altstack.mapping, altstack.mapping_size);
    altstack.mapping = nullptr;
    altstack.installed = false;
}

bool
install_current_thread_altstack(OwnedAltStack& altstack)
{
    stack_t current{};
    if (sigaltstack(nullptr, &current) != 0) {
        return false;
    }

    if (!(current.ss_flags & SS_DISABLE)) {
        // AIDEV-NOTE: CPython 3.14/crashtracking can preinstall an alt stack before profiling starts.
        // Reuse an existing stack instead of replacing it. This keeps CPU-timer mode compatible with
        // crashtracking/faulthandler-style owners without making us responsible for their stack lifetime.
        g_state.app_altstack_present.fetch_add(1, std::memory_order_relaxed);
        if (current.ss_size < usable_alt_stack_size()) {
            g_state.reused_altstack_too_small_count.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        g_state.reused_altstack_count.fetch_add(1, std::memory_order_relaxed);
        altstack.stack_sp = current.ss_sp;
        altstack.stack_size = current.ss_size;
        altstack.previous = current;
        altstack.installed = true;
        altstack.using_existing = true;
        return true;
    }

    const size_t guard_size = page_size();
    const size_t stack_size = usable_alt_stack_size();
    const size_t mapping_size = guard_size + stack_size;
    void* mapping = mmap(nullptr, mapping_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapping == MAP_FAILED) {
        return false;
    }

    if (mprotect(mapping, guard_size, PROT_NONE) != 0) {
        munmap(mapping, mapping_size);
        return false;
    }

    void* stack_sp = static_cast<char*>(mapping) + guard_size;
    stack_t ss{};
    ss.ss_sp = stack_sp;
    ss.ss_size = stack_size;
    ss.ss_flags = 0;
    if (sigaltstack(&ss, nullptr) != 0) {
        munmap(mapping, mapping_size);
        return false;
    }

    altstack.mapping = mapping;
    altstack.mapping_size = mapping_size;
    altstack.stack_sp = stack_sp;
    altstack.stack_size = stack_size;
    altstack.previous = current;
    altstack.installed = true;
    return true;
}

void
delete_timer(CaptureState& state)
{
    if (!state.timer_deleted && state.timer_id >= 0) {
        syscall(SYS_timer_delete, state.timer_id);
        state.timer_deleted = true;
        state.timer_id = -1;
    }
}

bool
create_timer(CaptureState& state)
{
    struct sigevent sev{};
    sev.sigev_notify = SIGEV_THREAD_ID;
    sev.sigev_signo = SIGPROF;
    sev.sigev_value.sival_ptr = &g_cookie;
    if (!set_sigev_thread_id(sev, static_cast<pid_t>(state.native_tid))) {
        errno = ENOTSUP;
        return false;
    }

    kernel_timer_id timer_id = -1;
    const long rc = syscall(SYS_timer_create, thread_cpu_clock(static_cast<pid_t>(state.native_tid)), &sev, &timer_id);
    if (rc != 0) {
        return false;
    }
    state.timer_id = timer_id;
    return true;
}

void
set_timespec_ns(struct timespec& ts, uint64_t ns)
{
    ts.tv_sec = static_cast<time_t>(ns / 1'000'000'000ULL);
    ts.tv_nsec = static_cast<long>(ns % 1'000'000'000ULL);
}

uint64_t
initial_timer_offset_ns(const CaptureState& state, uint64_t interval_ns)
{
    if (interval_ns <= 1) {
        return interval_ns;
    }

    uint64_t h = state.native_tid ^ (state.python_thread_id << 32);
    h ^= h >> 33;
    h *= 0xff51afd7ed558ccdULL;
    h ^= h >> 33;
    h *= 0xc4ceb9fe1a85ec53ULL;
    h ^= h >> 33;

    // Keep first-sample latency low for short-lived processes while still avoiding identical phase alignment.
    const uint64_t jitter_window_ns = std::min<uint64_t>(interval_ns, 1'000'000ULL);
    return 1 + (h % jitter_window_ns);
}

bool
arm_timer(CaptureState& state)
{
    struct itimerspec its{};
    const uint64_t interval_ns = g_state.interval_ms.load(std::memory_order_relaxed) * 1'000'000ULL;
    set_timespec_ns(its.it_value, initial_timer_offset_ns(state, interval_ns));
    set_timespec_ns(its.it_interval, interval_ns);
    return syscall(SYS_timer_settime, state.timer_id, 0, &its, nullptr) == 0;
}

void
clear_slot(CaptureState& state)
{
    const uint64_t tid = state.native_tid;
    if (tid > 0 && tid < g_state.slot_capacity) {
        g_state.slots[tid].store(nullptr, std::memory_order_seq_cst);
    }
}

bool
handler_inactive(CaptureState& state)
{
    const uint64_t tid = state.native_tid;
    if (tid == 0 || tid >= g_state.slot_capacity) {
        return true;
    }
    return !g_state.handler_active[tid].load(std::memory_order_seq_cst);
}

void
retire_state_locked(std::unique_ptr<CaptureState> state, bool current_thread)
{
    delete_timer(*state);
    clear_slot(*state);
    state->retired = true;

    if (current_thread) {
        const uint64_t now = thread_cpu_time_ns();
        if (now > state->last_cpu_ns) {
            state->residual_cpu_ns.fetch_add(now - state->last_cpu_ns, std::memory_order_relaxed);
        }
        disable_current_thread_altstack(state->altstack);
    } else if (state->altstack.installed && !state->altstack.using_existing && state->altstack.mapping != nullptr &&
               !state->altstack.detached) {
        state->altstack.detached = true;
        g_state.leaked_altstacks.emplace_back(state->altstack.mapping, state->altstack.mapping_size);
    }

    g_state.retired.push_back(std::move(state));
}

bool
initialize_slots()
{
    if (g_state.slot_capacity != 0) {
        return true;
    }

    const long pid_max = read_pid_max();
    const size_t capacity = static_cast<size_t>(pid_max) + 1;
    const size_t bytes = capacity * (sizeof(std::atomic<CaptureState*>) + sizeof(std::atomic<bool>));
    if (bytes > kMaxSlotBytes) {
        return false;
    }

    auto slots = std::make_unique<std::atomic<CaptureState*>[]>(capacity);
    auto active = std::make_unique<std::atomic<bool>[]>(capacity);
    for (size_t i = 0; i < capacity; i++) {
        slots[i].store(nullptr, std::memory_order_relaxed);
        active[i].store(false, std::memory_order_relaxed);
    }

    g_state.slots = std::move(slots);
    g_state.handler_active = std::move(active);
    g_state.slot_capacity = capacity;
    return true;
}

bool
validate_raw_code_object(const RawFrame& raw_frame)
{
    if (raw_frame.code_object == 0) {
        return false;
    }

    PyObject object_header{};
    auto* object_addr = reinterpret_cast<PyObject*>(raw_frame.code_object);
    if (copy_type(object_addr, object_header)) {
        return false;
    }
    if (object_header.ob_type != &PyCode_Type) {
        return false;
    }

    int first_lineno = 0;
    auto* firstlineno_addr = reinterpret_cast<decltype(PyCodeObject::co_firstlineno)*>(
      raw_frame.code_object + offsetof(PyCodeObject, co_firstlineno));
    if (copy_type(firstlineno_addr, first_lineno)) {
        return false;
    }
    return first_lineno == raw_frame.first_lineno;
}

void
render_raw_sample(EchionSampler& echion, CaptureState& state, const RawSample& raw)
{
    FrameStack stack;
    bool saw_invalid_frame = false;
    for (uint16_t i = 0; i < raw.depth; i++) {
        const RawFrame& raw_frame = raw.frames[i];
        if (!validate_raw_code_object(raw_frame)) {
            saw_invalid_frame = true;
            g_state.stage2_invalid_frame_count.fetch_add(1, std::memory_order_relaxed);
            continue;
        }
        auto maybe_frame = Frame::get(echion, reinterpret_cast<PyCodeObject*>(raw_frame.code_object), raw_frame.lasti);
        if (!maybe_frame) {
            continue;
        }
        Frame& frame = maybe_frame->get();
        if (&frame == &INVALID_FRAME || frame.first_lineno != raw_frame.first_lineno) {
            continue;
        }
        stack.push_back(frame);
    }

    if (stack.empty()) {
        if (!saw_invalid_frame) {
            return;
        }
        stack.push_back(UNKNOWN_FRAME);
    }

    const microsecond_t cpu_us = static_cast<microsecond_t>(raw.cpu_delta_ns / 1000ULL);
    if (cpu_us == 0) {
        return;
    }

    std::lock_guard<std::mutex> guard(echion.thread_info_map_lock());
    auto maybe_thread = echion.thread_info_map().find(raw.python_thread_id);
    if (maybe_thread != echion.thread_info_map().end()) {
        PyThreadState tstate{};
        PyThreadState* tstate_arg = nullptr;
        if (state.tstate != nullptr && !copy_type(state.tstate, tstate)) {
            tstate_arg = &tstate;
            maybe_thread->second->tstate_addr = reinterpret_cast<uintptr_t>(state.tstate);
        }
        auto success = maybe_thread->second->sample_cpu_timer(echion, tstate_arg, std::move(stack), cpu_us);
        if (success) {
            return;
        }
    }

    auto& renderer = echion.renderer();
    renderer.render_cpu_sample_begin(state.name, cpu_us, raw.python_thread_id, raw.native_tid);
    stack.render(echion);
    renderer.render_stack_end();
}

void
drain_state(EchionSampler& echion, CaptureState& state)
{
    RawSample raw;
    while (state.ring.pop_for_consumer(raw)) {
        render_raw_sample(echion, state, raw);
    }
}

void
disable_all_timers_locked()
{
    const pid_t current_tid = raw_gettid();
    for (auto it = g_state.live_by_thread_id.begin(); it != g_state.live_by_thread_id.end();) {
        auto state = std::move(it->second);
        const bool current_thread = current_tid == static_cast<pid_t>(state->native_tid);
        it = g_state.live_by_thread_id.erase(it);
        retire_state_locked(std::move(state), current_thread);
    }
}

void
disable_all_timers_for_hijack()
{
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    if (!g_state.active.load(std::memory_order_acquire)) {
        return;
    }
    g_state.handler_hijack_disable_count.fetch_add(1, std::memory_order_relaxed);
    g_state.active.store(false, std::memory_order_release);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    g_state.permanently_disabled.store(true, std::memory_order_release);
    disable_all_timers_locked();
}

void
disable_all_timers_for_blocked_signal()
{
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    g_state.active.store(false, std::memory_order_release);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    disable_all_timers_locked();
}

template<typename T>
bool
guarded_read_scalar(CaptureState& state, T& out, const T* src)
{
    if (src == nullptr) {
        return false;
    }

    if (sigsetjmp(state.fault_env, 0) != 0) {
        state.fault_armed = 0;
        __asm__ __volatile__("" ::: "memory");
        return false;
    }

    state.fault_armed = 1;
    __asm__ __volatile__("" ::: "memory");
    out = *src;
    __asm__ __volatile__("" ::: "memory");
    state.fault_armed = 0;
    return true;
}

bool
cpu_timer_fault_recover(int signo, siginfo_t* si, void* ucontext)
{
    (void)signo;
    (void)si;
    (void)ucontext;
    const int saved_errno = errno;

    const pid_t tid = raw_gettid();
    if (tid > 0 && static_cast<size_t>(tid) < g_state.slot_capacity) {
        CaptureState* state = g_state.slots[tid].load(std::memory_order_seq_cst);
        if (state != nullptr && state->fault_armed) {
            state->fault_armed = 0;
            errno = saved_errno;
            siglongjmp(state->fault_env, 1);
        }
    }

    errno = saved_errno;
    return false;
}

void
cpu_timer_signal_handler(int signo, siginfo_t* si, void* ucontext)
{
    // AIDEV-NOTE: This handler may run on arbitrary profiled Python threads. Keep it async-signal-safe:
    // no allocation, no locks, no GIL, and keep the seq_cst active/slot handshake paired with retire_state_locked.
    const int saved_errno = errno;

    const bool accepted = si != nullptr && si->si_code == SI_TIMER && si->si_value.sival_ptr == &g_cookie;
    if (!accepted) {
        forward_foreign_sigprof(signo, si, ucontext);
        errno = saved_errno;
        return;
    }

    if (!g_state.active.load(std::memory_order_acquire)) {
        errno = saved_errno;
        return;
    }

    const pid_t tid = raw_gettid();
    if (tid <= 0 || static_cast<size_t>(tid) >= g_state.slot_capacity) {
        g_state.accepted_signal_oob_tid_count.fetch_add(1, std::memory_order_relaxed);
        errno = saved_errno;
        return;
    }

    g_state.handler_active[tid].store(true, std::memory_order_seq_cst);
    CaptureState* state = g_state.slots[tid].load(std::memory_order_seq_cst);
    if (state == nullptr) {
        g_state.handler_active[tid].store(false, std::memory_order_seq_cst);
        errno = saved_errno;
        return;
    }

    const uint64_t now = thread_cpu_time_ns();
    uint64_t delta = 0;
    if (now > state->last_cpu_ns) {
        delta = now - state->last_cpu_ns;
    }
    state->last_cpu_ns = now;

    RawSample* sample = state->ring.reserve_for_producer();
    if (sample == nullptr) {
        state->dropped_count.fetch_add(1, std::memory_order_relaxed);
        state->dropped_cpu_ns.fetch_add(delta, std::memory_order_relaxed);
        g_state.handler_active[tid].store(false, std::memory_order_seq_cst);
        errno = saved_errno;
        return;
    }

    sample->cpu_delta_ns = delta;
    sample->python_thread_id = state->python_thread_id;
    sample->native_tid = state->native_tid;
    sample->depth = 0;

    bool failed = false;
    PyThreadState* tstate = state->tstate;
    DataDog::py_frame_t* frame = nullptr;
    if (tstate == nullptr || !guarded_read_scalar(*state, frame, &tstate->current_frame)) {
        failed = true;
    }

    uint16_t walked = 0;
    while (!failed && frame != nullptr && sample->depth < kMaxCpuTimerFrames && walked < kMaxCpuTimerFrames) {
        walked++;
        decltype(frame->owner) owner{};
        DataDog::py_frame_t* previous = nullptr;
        if (!guarded_read_scalar(*state, owner, &frame->owner) ||
            !guarded_read_scalar(*state, previous, &frame->previous)) {
            failed = true;
            break;
        }

        if (owner == FRAME_OWNED_BY_CSTACK || owner == FRAME_OWNED_BY_INTERPRETER) {
            frame = previous;
            continue;
        }
        if (owner != FRAME_OWNED_BY_THREAD && owner != FRAME_OWNED_BY_GENERATOR) {
            failed = true;
            break;
        }

        decltype(frame->f_executable.bits) executable_bits{};
        _Py_CODEUNIT* instr_ptr = nullptr;
        if (!guarded_read_scalar(*state, executable_bits, &frame->f_executable.bits) ||
            !guarded_read_scalar(*state, instr_ptr, &frame->instr_ptr)) {
            failed = true;
            break;
        }

        PyCodeObject* code = reinterpret_cast<PyCodeObject*>(static_cast<uintptr_t>(executable_bits) &
                                                             ~static_cast<uintptr_t>(7));
        if (code == nullptr || instr_ptr == nullptr) {
            failed = true;
            break;
        }

        int first_lineno = 0;
        if (!guarded_read_scalar(*state, first_lineno, &code->co_firstlineno)) {
            failed = true;
            break;
        }

        const int lasti = static_cast<int>((instr_ptr - reinterpret_cast<_Py_CODEUNIT*>(code)) -
                                           static_cast<ptrdiff_t>(offsetof(PyCodeObject, co_code_adaptive) /
                                                                  sizeof(_Py_CODEUNIT)));
        RawFrame& raw_frame = sample->frames[sample->depth];
        raw_frame.code_object = reinterpret_cast<uintptr_t>(code);
        raw_frame.lasti = lasti;
        raw_frame.first_lineno = first_lineno;
        sample->depth++;
        frame = previous;
    }

    if (failed || sample->depth == 0) {
        state->capture_failed_count.fetch_add(1, std::memory_order_relaxed);
        state->capture_failed_cpu_ns.fetch_add(delta, std::memory_order_relaxed);
        g_state.handler_active[tid].store(false, std::memory_order_seq_cst);
        errno = saved_errno;
        return;
    }

    state->ring.publish_for_producer();
    g_state.handler_active[tid].store(false, std::memory_order_seq_cst);
    errno = saved_errno;
}

#endif // DD_CPU_TIMER_SUPPORTED

} // namespace

Engine&
Engine::get()
{
    static Engine engine;
    return engine;
}

void
Engine::configure(bool enabled, uint64_t interval_ms)
{
#if DD_CPU_TIMER_SUPPORTED
    if (interval_ms < kMinIntervalMs) {
        interval_ms = kMinIntervalMs;
    }
    g_state.interval_ms.store(interval_ms, std::memory_order_release);
    g_state.configured.store(enabled, std::memory_order_release);
#else
    (void)enabled;
    (void)interval_ms;
#endif
}

bool
Engine::start()
{
#if DD_CPU_TIMER_SUPPORTED
    if (!g_state.configured.load(std::memory_order_acquire)) {
        return false;
    }
    if (g_state.permanently_disabled.load(std::memory_order_acquire) ||
        g_state.stopped_once.load(std::memory_order_acquire) ||
        g_state.blocked_signal_count.load(std::memory_order_acquire) > 0) {
        return false;
    }
    if (g_state.active.load(std::memory_order_acquire)) {
        return true;
    }

    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    if (g_state.active.load(std::memory_order_acquire)) {
        return true;
    }
    if (g_state.blocked_signal_count.load(std::memory_order_acquire) > 0) {
        return false;
    }
    if (!initialize_slots()) {
        g_state.permanently_disabled.store(true, std::memory_order_release);
        return false;
    }
    register_profiling_fault_recover(cpu_timer_fault_recover);
    if (init_profiling_fault_handler() != 0) {
        unregister_profiling_fault_recover(cpu_timer_fault_recover);
        g_state.permanently_disabled.store(true, std::memory_order_release);
        return false;
    }
    if (!install_sigprof_handler()) {
        unregister_profiling_fault_recover(cpu_timer_fault_recover);
        g_state.permanently_disabled.store(true, std::memory_order_release);
        return false;
    }
    if (!g_state.exit_handler_registered) {
        std::atexit(cpu_timer_process_exit);
        g_state.exit_handler_registered = true;
    }
    g_state.started_once.store(true, std::memory_order_release);
    g_state.active.store(true, std::memory_order_release);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    return true;
#else
    return false;
#endif
}

void
Engine::shutdown(EchionSampler& echion)
{
#if DD_CPU_TIMER_SUPPORTED
    {
        std::lock_guard<std::mutex> lock(g_state.registry_lock);
        if (!g_state.started_once.load(std::memory_order_acquire)) {
            return;
        }
        g_state.active.store(false, std::memory_order_release);
        g_state.replacing_wall_cpu.store(false, std::memory_order_release);
        g_state.stopped_once.store(true, std::memory_order_release);

        for (auto it = g_state.live_by_thread_id.begin(); it != g_state.live_by_thread_id.end();) {
            auto state = std::move(it->second);
            const bool current_thread = raw_gettid() == static_cast<pid_t>(state->native_tid);
            it = g_state.live_by_thread_id.erase(it);
            retire_state_locked(std::move(state), current_thread);
        }
    }

    drain(echion);
#else
    (void)echion;
#endif
}

void
Engine::disable_for_fault_handler_swap()
{
#if DD_CPU_TIMER_SUPPORTED
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    if (!g_state.active.load(std::memory_order_acquire)) {
        return;
    }
    g_state.handler_hijack_disable_count.fetch_add(1, std::memory_order_relaxed);
    g_state.active.store(false, std::memory_order_release);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    g_state.permanently_disabled.store(true, std::memory_order_release);
    disable_all_timers_locked();
#endif
}

void
Engine::postfork_child()
{
#if DD_CPU_TIMER_SUPPORTED
    new (&g_state.registry_lock) std::mutex();
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    const bool was_active = g_state.active.load(std::memory_order_acquire);
    g_state.active.store(false, std::memory_order_release);
    g_state.replacing_wall_cpu.store(false, std::memory_order_release);

    const pid_t current_tid = raw_gettid();
    for (auto& item : g_state.live_by_thread_id) {
        CaptureState& state = *item.second;
        if (current_tid == static_cast<pid_t>(state.native_tid)) {
            disable_current_thread_altstack(state.altstack);
        } else if (state.altstack.installed && !state.altstack.using_existing && !state.altstack.detached &&
                   state.altstack.mapping != nullptr) {
            munmap(state.altstack.mapping, state.altstack.mapping_size);
            state.altstack.mapping = nullptr;
        }
    }
    for (auto& state : g_state.retired) {
        if (state->altstack.installed && !state->altstack.using_existing && !state->altstack.detached &&
            state->altstack.mapping != nullptr) {
            munmap(state->altstack.mapping, state->altstack.mapping_size);
            state->altstack.mapping = nullptr;
        }
    }
    for (auto& leaked : g_state.leaked_altstacks) {
        munmap(leaked.first, leaked.second);
    }
    g_state.leaked_altstacks.clear();
    g_state.live_by_thread_id.clear();
    g_state.retired.clear();
    if (g_state.slot_capacity != 0) {
        for (size_t i = 0; i < g_state.slot_capacity; i++) {
            g_state.slots[i].store(nullptr, std::memory_order_relaxed);
            g_state.handler_active[i].store(false, std::memory_order_relaxed);
        }
    }
    if (was_active && !g_state.stopped_once.load(std::memory_order_acquire) &&
        !g_state.permanently_disabled.load(std::memory_order_acquire)) {
        register_profiling_fault_recover(cpu_timer_fault_recover);
        init_profiling_fault_handler();
        install_sigprof_handler();
        g_state.active.store(true, std::memory_order_release);
        g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    }
#endif
}

void
Engine::register_thread(uint64_t python_thread_id, uint64_t native_id, const char* name, PyThreadState* tstate)
{
#if DD_CPU_TIMER_SUPPORTED
    if (!g_state.active.load(std::memory_order_acquire)) {
        return;
    }

    const pid_t current_tid = raw_gettid();
    const bool current_thread = native_id == static_cast<uint64_t>(current_tid);
    // AIDEV-NOTE: Remote arming cannot inspect the target thread's signal mask.
    // If current-thread registration already found SIGPROF/SIGSEGV/SIGBUS blocked,
    // keep the CPU timer inactive rather than replacing wall-sampled CPU time unsafely.
    if (!current_thread && g_state.blocked_signal_count.load(std::memory_order_relaxed) > 0) {
        return;
    }
    if (tstate == nullptr) {
        g_state.pending_unprepared.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (native_id == 0 || native_id >= g_state.slot_capacity) {
        g_state.tid_out_of_bounds.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (current_thread && (is_signal_blocked(SIGPROF) || is_signal_blocked(SIGSEGV) || is_signal_blocked(SIGBUS))) {
        g_state.blocked_signal_count.fetch_add(1, std::memory_order_relaxed);
        disable_all_timers_for_blocked_signal();
        return;
    }
    if (!sigprof_handler_still_installed() || !fault_handlers_still_installed()) {
        disable_all_timers_for_hijack();
        return;
    }

    auto state = std::make_unique<CaptureState>(python_thread_id, native_id, name);
    state->tstate = g_state.fault_injection_enabled.load(std::memory_order_acquire)
                      ? reinterpret_cast<PyThreadState*>(static_cast<uintptr_t>(1))
                      : tstate;

    if (current_thread && !install_current_thread_altstack(state->altstack)) {
        return;
    }
    if (!create_timer(*state)) {
        g_state.timer_syscall_failures.fetch_add(1, std::memory_order_relaxed);
        if (current_thread) {
            disable_current_thread_altstack(state->altstack);
        }
        return;
    }

    state->last_cpu_ns = current_thread ? thread_cpu_time_ns() : thread_cpu_time_ns(static_cast<pid_t>(native_id));
    CaptureState* state_ptr = state.get();
    {
        std::lock_guard<std::mutex> lock(g_state.registry_lock);
        if (!g_state.active.load(std::memory_order_acquire)) {
            delete_timer(*state);
            if (current_thread) {
                disable_current_thread_altstack(state->altstack);
            }
            return;
        }
        auto existing = g_state.live_by_thread_id.find(python_thread_id);
        if (existing != g_state.live_by_thread_id.end()) {
            auto old = std::move(existing->second);
            g_state.live_by_thread_id.erase(existing);
            retire_state_locked(std::move(old), current_thread);
        }
        g_state.live_by_thread_id.emplace(python_thread_id, std::move(state));
        g_state.slots[native_id].store(state_ptr, std::memory_order_seq_cst);
    }

    if (!arm_timer(*state_ptr)) {
        g_state.timer_syscall_failures.fetch_add(1, std::memory_order_relaxed);
        unregister_thread(python_thread_id);
        return;
    }
    g_state.replacing_wall_cpu.store(true, std::memory_order_release);
#else
    (void)python_thread_id;
    (void)native_id;
    (void)name;
    (void)tstate;
#endif
}

void
Engine::unregister_thread(uint64_t python_thread_id)
{
#if DD_CPU_TIMER_SUPPORTED
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    auto it = g_state.live_by_thread_id.find(python_thread_id);
    if (it == g_state.live_by_thread_id.end()) {
        return;
    }
    auto state = std::move(it->second);
    g_state.live_by_thread_id.erase(it);
    const bool current_thread = raw_gettid() == static_cast<pid_t>(state->native_tid);
    retire_state_locked(std::move(state), current_thread);
    if (g_state.live_by_thread_id.empty()) {
        g_state.replacing_wall_cpu.store(false, std::memory_order_release);
    }
#else
    (void)python_thread_id;
#endif
}

bool
Engine::has_thread(uint64_t python_thread_id, uint64_t native_id) const
{
#if DD_CPU_TIMER_SUPPORTED
    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    const auto it = g_state.live_by_thread_id.find(python_thread_id);
    if (it == g_state.live_by_thread_id.end()) {
        return false;
    }
    return it->second->native_tid == native_id;
#else
    (void)python_thread_id;
    (void)native_id;
    return false;
#endif
}

void
Engine::drain(EchionSampler& echion)
{
#if DD_CPU_TIMER_SUPPORTED
    if (g_state.active.load(std::memory_order_acquire) &&
        (!sigprof_handler_still_installed() || !fault_handlers_still_installed())) {
        disable_all_timers_for_hijack();
    }

    std::vector<CaptureState*> live;
    std::vector<std::unique_ptr<CaptureState>> ready_to_free;
    {
        std::lock_guard<std::mutex> lock(g_state.registry_lock);
        live.reserve(g_state.live_by_thread_id.size());
        for (auto& item : g_state.live_by_thread_id) {
            live.push_back(item.second.get());
        }

        auto it = g_state.retired.begin();
        while (it != g_state.retired.end()) {
            if (handler_inactive(**it)) {
                ready_to_free.push_back(std::move(*it));
                it = g_state.retired.erase(it);
            } else {
                ++it;
            }
        }
    }

    for (CaptureState* state : live) {
        drain_state(echion, *state);
    }
    for (auto& state : ready_to_free) {
        drain_state(echion, *state);
    }
#else
    (void)echion;
#endif
}

bool
Engine::replaces_wall_sampler_cpu_time() const
{
#if DD_CPU_TIMER_SUPPORTED
    return g_state.replacing_wall_cpu.load(std::memory_order_acquire);
#else
    return false;
#endif
}

bool
Engine::configured_enabled() const
{
#if DD_CPU_TIMER_SUPPORTED
    return g_state.configured.load(std::memory_order_acquire);
#else
    return false;
#endif
}

microsecond_t
Engine::interval_us() const
{
#if DD_CPU_TIMER_SUPPORTED
    return static_cast<microsecond_t>(g_state.interval_ms.load(std::memory_order_acquire) * 1000ULL);
#else
    return 0;
#endif
}

DebugStats
Engine::debug_stats() const
{
    DebugStats stats{};
#if DD_CPU_TIMER_SUPPORTED
    stats.supported = true;
    stats.configured = g_state.configured.load(std::memory_order_acquire);
    stats.active = g_state.active.load(std::memory_order_acquire);
    stats.permanently_disabled = g_state.permanently_disabled.load(std::memory_order_acquire);
    stats.replacing_wall_cpu = g_state.replacing_wall_cpu.load(std::memory_order_acquire);
    stats.interval_ms = g_state.interval_ms.load(std::memory_order_acquire);
    stats.pending_unprepared = g_state.pending_unprepared.load(std::memory_order_relaxed);
    stats.app_altstack_present = g_state.app_altstack_present.load(std::memory_order_relaxed);
    stats.reused_altstack_count = g_state.reused_altstack_count.load(std::memory_order_relaxed);
    stats.reused_altstack_too_small_count = g_state.reused_altstack_too_small_count.load(std::memory_order_relaxed);
    stats.blocked_signal_count = g_state.blocked_signal_count.load(std::memory_order_relaxed);
    stats.tid_out_of_bounds = g_state.tid_out_of_bounds.load(std::memory_order_relaxed);
    stats.timer_syscall_failures = g_state.timer_syscall_failures.load(std::memory_order_relaxed);
    stats.accepted_signal_oob_tid_count = g_state.accepted_signal_oob_tid_count.load(std::memory_order_relaxed);
    stats.handler_hijack_disable_count = g_state.handler_hijack_disable_count.load(std::memory_order_relaxed);
    stats.fast_copy_conflict_count = g_state.fast_copy_conflict_count.load(std::memory_order_relaxed);
    stats.stage2_invalid_frame_count = g_state.stage2_invalid_frame_count.load(std::memory_order_relaxed);

    std::lock_guard<std::mutex> lock(g_state.registry_lock);
    stats.live_count = g_state.live_by_thread_id.size();
    stats.retired_count = g_state.retired.size();
    stats.leaked_altstack_count = g_state.leaked_altstacks.size();
    for (const auto& item : g_state.live_by_thread_id) {
        const CaptureState& state = *item.second;
        stats.dropped_count += state.dropped_count.load(std::memory_order_relaxed);
        stats.dropped_cpu_ns += state.dropped_cpu_ns.load(std::memory_order_relaxed);
        stats.capture_failed_count += state.capture_failed_count.load(std::memory_order_relaxed);
        stats.capture_failed_cpu_ns += state.capture_failed_cpu_ns.load(std::memory_order_relaxed);
        stats.residual_cpu_ns += state.residual_cpu_ns.load(std::memory_order_relaxed);
    }
    for (const auto& state_ptr : g_state.retired) {
        const CaptureState& state = *state_ptr;
        stats.dropped_count += state.dropped_count.load(std::memory_order_relaxed);
        stats.dropped_cpu_ns += state.dropped_cpu_ns.load(std::memory_order_relaxed);
        stats.capture_failed_count += state.capture_failed_count.load(std::memory_order_relaxed);
        stats.capture_failed_cpu_ns += state.capture_failed_cpu_ns.load(std::memory_order_relaxed);
        stats.residual_cpu_ns += state.residual_cpu_ns.load(std::memory_order_relaxed);
    }
#endif
    return stats;
}

void
Engine::debug_set_fault_injection(bool enabled)
{
#if DD_CPU_TIMER_SUPPORTED
    g_state.fault_injection_enabled.store(enabled, std::memory_order_release);
#else
    (void)enabled;
#endif
}

} // namespace CpuTimer
} // namespace Datadog
