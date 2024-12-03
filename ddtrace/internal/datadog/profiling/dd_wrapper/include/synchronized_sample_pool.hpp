#pragma once

#include "sample.hpp"

extern "C"
{
#include "datadog/common.h"
}
#include <memory>
#include <optional>
#include <unistd.h>

namespace Datadog {

static int tripcount = 0;

struct Deleter
{
    void operator()(ddog_ArrayQueue* object)
    {
        tripcount++;
        std::cerr << getpid() << ": delter called, count " << tripcount << std::endl;
        uintptr_t* p = reinterpret_cast<uintptr_t*>(object);
        *p = 0xdeadbeefdeadbeef;
        // ddog_ArrayQueue_drop(object);
    }
};

class SynchronizedSamplePool
{
  private:
    std::unique_ptr<ddog_ArrayQueue, Deleter> pool;

  public:
    SynchronizedSamplePool(size_t capacity);
    //~SynchronizedSamplePool();

    std::optional<Sample*> take_sample();
    std::optional<Sample*> return_sample(Sample* sample);
};
} // namespace Datadog
