#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>

namespace Datadog {
namespace CpuTimer {

// AIDEV-NOTE: Keep this intentionally below echion's 2048-frame wall-sampler cap.
// RawSample is embedded in every preallocated ring slot, so high frame caps multiply
// into large per-thread resident memory and can destabilize thread-heavy processes.
constexpr uint16_t kMaxCpuTimerFrames = 512;

struct RawFrame
{
    uintptr_t code_object = 0;
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

class CpuSampleRing
{
    const uint32_t capacity_;
    std::unique_ptr<RawSample[]> samples_;
    std::atomic<uint32_t> head_{ 0 };
    std::atomic<uint32_t> tail_{ 0 };

  public:
    explicit CpuSampleRing(uint32_t capacity)
      : capacity_(capacity)
      , samples_(std::make_unique<RawSample[]>(capacity))
    {
    }

    CpuSampleRing(const CpuSampleRing&) = delete;
    CpuSampleRing& operator=(const CpuSampleRing&) = delete;

    uint32_t capacity() const { return capacity_; }

    RawSample* reserve_for_producer()
    {
        const uint32_t head = head_.load(std::memory_order_relaxed);
        const uint32_t next = (head + 1) % capacity_;
        const uint32_t tail = tail_.load(std::memory_order_acquire);
        if (next == tail) {
            return nullptr;
        }
        return &samples_[head];
    }

    void publish_for_producer()
    {
        const uint32_t head = head_.load(std::memory_order_relaxed);
        head_.store((head + 1) % capacity_, std::memory_order_release);
    }

    bool pop_for_consumer(RawSample& out)
    {
        const uint32_t tail = tail_.load(std::memory_order_relaxed);
        const uint32_t head = head_.load(std::memory_order_acquire);
        if (tail == head) {
            return false;
        }
        out = samples_[tail];
        tail_.store((tail + 1) % capacity_, std::memory_order_release);
        return true;
    }

    bool empty() const { return tail_.load(std::memory_order_acquire) == head_.load(std::memory_order_acquire); }
};

} // namespace CpuTimer
} // namespace Datadog
