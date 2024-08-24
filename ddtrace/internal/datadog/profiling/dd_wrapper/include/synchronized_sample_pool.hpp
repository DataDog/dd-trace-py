#pragma once

#include "sample.hpp"

extern "C"
{
#include "datadog/common.h"
}
#include <memory>
#include <optional>

namespace Datadog {

struct Deleter
{
    void operator()(ddog_ArrayQueue* object) { ddog_ArrayQueue_drop(object); }
};

class SynchronizedSamplePool
{
  private:
    std::unique_ptr<ddog_ArrayQueue, Deleter> pool;

  public:
    SynchronizedSamplePool(size_t capacity);

    std::optional<Sample*> take_sample();
    std::optional<Sample*> return_sample(Sample* sample);
};
} // namespace Datadog
