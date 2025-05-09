#include "static_sample_pool.hpp"
#include "sample.hpp"
#include <optional>

namespace Datadog {

Sample* StaticSamplePool::pool[StaticSamplePool::CAPACITY] = { nullptr };
std::mutex StaticSamplePool::mutex;
int StaticSamplePool::head = -1;

std::optional<Sample*> StaticSamplePool::take_sample()
{
    mutex.lock();
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
    mutex.lock();
    if (head + 1 >= static_cast<int>(CAPACITY)) {
        return sample;
    }
    ++head;
    pool[head] = sample;
    return std::nullopt;
}

void StaticSamplePool::postfork_child()
{
    if (mutex.try_lock()) {
        mutex.unlock();
    }
}

} // namespace Datadog
