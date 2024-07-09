#pragma once

#include "sample.hpp"

#include <memory>
#include <optional>

#include "boost/lockfree/queue.hpp"

namespace Datadog {

class SynchronizedSamplePool
{
  private:
    boost::lockfree::queue<Sample*> pool;

  public:
    SynchronizedSamplePool()
      : pool(128) {};

    std::optional<Sample*> get_sample();
    void return_sample(Sample* sample);
};
} // namespace DataDdog
