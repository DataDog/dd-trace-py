#include "synchronized_sample_pool.hpp"

#include "sample.hpp"

namespace Datadog {
std::optional<Sample*>
SynchronizedSamplePool::get_sample()
{
    const std::lock_guard<std::mutex> lock(mutex);
    if (!pool.empty()) {
        // Reuse a sample from the pool
        auto sample = pool.back().release();
        pool.pop_back();
        return sample;
    }
    return std::nullopt;
}

void
SynchronizedSamplePool::return_sample(Sample* sample)
{
    const std::lock_guard<std::mutex> lock(mutex);
    // Return the sample to the pool
    pool.emplace_back(sample); // NOLINT(cppcoreguidelines-owning-memory)
}
} // namespace Datadog
