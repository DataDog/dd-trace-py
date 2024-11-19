#pragma once

#include "sample.hpp"

#include "vendored/concurrentqueue.h"

#include <memory>
#include <optional>

namespace Datadog {

class SynchronizedSamplePool
{
  private:
    moodycamel::ConcurrentQueue<Sample*> pool;
    std::atomic<size_t> capacity;

  public:
    SynchronizedSamplePool(size_t _capacity)
    {
        capacity.store(_capacity);
        pool = moodycamel::ConcurrentQueue<Sample*>(_capacity);
    }

    ~SynchronizedSamplePool()
    {
        // number of successive calls to try_dequeue that returned false
        std::atomic<size_t> cnt{ 0 };

        while (cnt.load() < capacity.load()) {
            Sample* sample = nullptr;
            if (pool.try_dequeue(sample) && sample != nullptr) {
                delete sample;
                cnt.store(0);
            } else {
                cnt.fetch_add(1);
                // Explicitly yield to make sure that other threads trying to
                // push to the pool can proceed.
                std::this_thread::yield();
            }
        }
    }

    std::optional<Sample*> take_sample();
    std::optional<Sample*> return_sample(Sample* sample);
};
} // namespace Datadog
