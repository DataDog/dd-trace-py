#include "static_sample_pool.hpp"
#include "sample.hpp"
#include <optional>
#include <atomic>
#include <cstddef>

namespace Datadog {

static constexpr std::size_t CAPACITY = StaticSamplePool::CAPACITY;
static std::atomic<Sample*> pool[CAPACITY];

struct StaticSamplePoolInitializer {
    StaticSamplePoolInitializer() {
        for (std::size_t i = 0; i < CAPACITY; ++i) {
            pool[i].store(nullptr, std::memory_order_relaxed);
        }
    }
} initializer;

std::optional<Sample*> StaticSamplePool::take_sample() {
    for (std::size_t i = 0; i < CAPACITY; ++i) {
        Sample* s = pool[i].exchange(nullptr, std::memory_order_acq_rel);
        if (s != nullptr) {
            return s;
        }
    }
    return std::nullopt;
}

std::optional<Sample*> StaticSamplePool::return_sample(Sample* sample) {
    for (std::size_t i = 0; i < CAPACITY; ++i) {
        Sample* expected = nullptr;
        if (pool[i].compare_exchange_strong(expected, sample,
                                            std::memory_order_acq_rel,
                                            std::memory_order_relaxed)) {
            return std::nullopt;
        }
    }
    return sample;
}
} // namespace Datadog
