#include "sample.hpp"
#include "synchronized_sample_pool.hpp"

#include "boost/lockfree/queue.hpp"

namespace Datadog {
std::optional<Sample*>
SynchronizedSamplePool::get_sample()
{
    Sample* sample = nullptr;
    if (pool.pop(sample)) {
        return sample;
    }

    return std::nullopt;
}

void
SynchronizedSamplePool::return_sample(Sample* sample)
{
    pool.push(sample);
}
} // namespace Datadog
