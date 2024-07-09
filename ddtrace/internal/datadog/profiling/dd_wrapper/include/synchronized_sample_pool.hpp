#pragma once

#include "sample.hpp"

#include <memory>
#include <mutex>
#include <optional>
#include <vector>

namespace Datadog {

class SynchronizedSamplePool
{
  private:
    std::mutex mutex;
    std::vector<std::unique_ptr<Sample>> pool;

  public:
    SynchronizedSamplePool() = default;

    std::optional<Sample*> get_sample();
    void return_sample(Sample* sample);
};
} // namespace DataDdog
