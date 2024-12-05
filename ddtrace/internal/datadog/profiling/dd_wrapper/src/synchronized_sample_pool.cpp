#include "synchronized_sample_pool.hpp"

#include "libdatadog_helpers.hpp"

#include "vendored/concurrentqueue.h"

namespace Datadog {

std::optional<Sample*>
SynchronizedSamplePool::take_sample()
{
    const std::lock_guard<std::mutex> lock(mtx);
    if (!pool.empty()) {
        // Reuse a sample from the pool
        auto sample = pool.back().release();
        pool.pop_back();
        return sample;
    }
    return std::nullopt;
}

std::optional<Sample*>
SynchronizedSamplePool::return_sample(Sample* sample)
{
    const std::lock_guard<std::mutex> lock(mtx);
    // We don't want the pool to grow without a bound, so check the size and
    // discard the sample if it's larger than capacity.
    if (capacity <= pool.size()) {
        return sample;
    } else {
        pool.emplace_back(sample);
        return std::nullopt;
    }
}
} // namespace Datadog
