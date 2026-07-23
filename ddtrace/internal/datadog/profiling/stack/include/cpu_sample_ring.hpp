#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <type_traits>

namespace Datadog {
namespace CpuTimer {

// Keep this intentionally below echion's 2048-frame wall-sampler cap. RawSample
// is embedded in every preallocated ring slot, so high frame caps multiply into
// large per-thread resident memory and can destabilize thread-heavy processes.
constexpr uint16_t kMaxCpuTimerFrames = 512;

struct RawFrame
{
    void* code_object = nullptr;
    int lasti = -1;
    int first_lineno = 0;
};

struct RawSample
{
    uint64_t cpu_delta_ns = 0;
    uint64_t python_thread_id = 0;
    uint64_t native_tid = 0;
    uint16_t depth = 0;
    std::array<RawFrame, kMaxCpuTimerFrames> frames{};
};

static_assert(std::is_trivially_copyable<RawSample>::value, "ring slots must be safe to copy without allocation");
static_assert(std::atomic<uint32_t>::is_always_lock_free,
              "CPU timer signal handler requires lock-free ring index atomics");

// Single-producer, single-consumer ring. Each CaptureState owns one ring, so
// its SIGEV_THREAD_ID target is the sole producer and the sampler thread is the
// sole consumer. The producer must either publish a reserved slot or abandon it
// before its next reservation. An abandoned slot remains invisible and can be
// overwritten by the next reservation.
class CpuSampleRing
{
    static constexpr uint32_t kMinimumCapacity = 2;

    const uint32_t capacity_;
    std::unique_ptr<RawSample[]> samples_;
    std::atomic<uint32_t> head_{ 0 };
    std::atomic<uint32_t> tail_{ 0 };

    uint32_t advance(uint32_t index) const noexcept
    {
        const uint32_t next = index + 1;
        return next == capacity_ ? 0 : next;
    }

  public:
    explicit CpuSampleRing(uint32_t capacity)
      : capacity_(capacity < kMinimumCapacity ? kMinimumCapacity : capacity)
      , samples_(std::make_unique<RawSample[]>(capacity_))
    {
    }

    CpuSampleRing(const CpuSampleRing&) = delete;
    CpuSampleRing& operator=(const CpuSampleRing&) = delete;

    [[nodiscard]] uint32_t capacity() const noexcept { return capacity_; }

    // The acquire load pairs with the consumer's release store after it has
    // finished copying a slot, so the producer never overwrites an in-use slot.
    [[nodiscard]] RawSample* reserve_for_producer() noexcept
    {
        const uint32_t head = head_.load(std::memory_order_relaxed);
        const uint32_t next = advance(head);
        const uint32_t tail = tail_.load(std::memory_order_acquire);
        if (next == tail) {
            return nullptr;
        }
        return &samples_[head];
    }

    // The release store publishes all slot writes to the consumer.
    void publish_for_producer() noexcept
    {
        const uint32_t head = head_.load(std::memory_order_relaxed);
        head_.store(advance(head), std::memory_order_release);
    }

    // The acquire load pairs with publish_for_producer(), so the consumer sees
    // a complete RawSample before copying it.
    [[nodiscard]] bool pop_for_consumer(RawSample& out) noexcept
    {
        const uint32_t tail = tail_.load(std::memory_order_relaxed);
        const uint32_t head = head_.load(std::memory_order_acquire);
        if (tail == head) {
            return false;
        }
        out = samples_[tail];
        tail_.store(advance(tail), std::memory_order_release);
        return true;
    }

    // This is a best-effort snapshot. It must not be used to coordinate work.
    [[nodiscard]] bool empty() const noexcept
    {
        return tail_.load(std::memory_order_acquire) == head_.load(std::memory_order_acquire);
    }
};

} // namespace CpuTimer
} // namespace Datadog
