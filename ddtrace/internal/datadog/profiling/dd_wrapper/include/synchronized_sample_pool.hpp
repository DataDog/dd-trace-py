#pragma once

#include "sample.hpp"

#include <memory>
#include <optional>

namespace Datadog {

class SynchronizedSamplePool
{
  private:
    std::mutex mtx;
    std::vector<std::unique_ptr<Sample>> pool;
    size_t capacity;
    void clear();

  public:
    SynchronizedSamplePool(size_t _capacity)
      : capacity(_capacity)
    {
    }

    std::optional<Sample*> take_sample();
    std::optional<Sample*> return_sample(Sample* sample);
    void postfork_child();
};
} // namespace Datadog
