#include "synchronized_sample_pool.hpp"

#include "libdatadog_helpers.hpp"

#include "vendored/concurrentqueue.h"

namespace Datadog {

std::optional<Sample*>
SynchronizedSamplePool::take_sample()
{
    std::unique_ptr<Sample> sample = nullptr;

    pool.try_dequeue(sample);

    if (sample == nullptr) {
        return std::nullopt;
    }
    return sample.release();
}

std::optional<Sample*>
SynchronizedSamplePool::return_sample(Sample* sample)
{
    // We don't want the pool to grow without a bound, so check the size and
    // discard the sample if it's larger than capacity.
    if (capacity.load() <= pool.size_approx()) {
        return sample;
    } else {
        std::unique_ptr<Sample> ptr = nullptr;
        ptr.reset(sample);
        pool.enqueue(std::move(ptr));
        return std::nullopt;
    }
}
} // namespace Datadog
