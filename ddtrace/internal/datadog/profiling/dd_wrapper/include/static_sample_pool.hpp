#pragma once

#include <cstddef>
#include <optional>

#include "sample.hpp"

namespace Datadog {

class StaticSamplePool
{
  public:
    static constexpr std::size_t CAPACITY = g_default_sample_pool_capacity;

    static std::optional<Sample*> take_sample();
    static std::optional<Sample*> return_sample(Sample* sample);

  private:
    StaticSamplePool() = delete;
    StaticSamplePool(const StaticSamplePool&) = delete;
    StaticSamplePool& operator=(const StaticSamplePool&) = delete;
};

} // namespace Datadog
