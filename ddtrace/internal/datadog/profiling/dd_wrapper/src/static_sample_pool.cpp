#include "static_sample_pool.hpp"
#include "sample.hpp"
#include <optional>

namespace Datadog {

Sample* StaticSamplePool::pool[StaticSamplePool::CAPACITY] = { nullptr };
std::mutex StaticSamplePool::mutex;
int StaticSamplePool::head = -1;

std::optional<Sample*> StaticSamplePool::take_sample()
{
    std::lock_guard<std::mutex> lock(mutex);
    if (head < 0) {
        return std::nullopt;
    }
    Sample* s = pool[head];
    pool[head] = nullptr;
    --head;
    return s;
}

std::optional<Sample*> StaticSamplePool::return_sample(Sample* sample)
{
    std::lock_guard<std::mutex> lock(mutex);
    if (head + 1 >= static_cast<int>(CAPACITY)) {
        return sample;
    }
    ++head;
    pool[head] = sample;
    return std::nullopt;
}

} // namespace Datadog
