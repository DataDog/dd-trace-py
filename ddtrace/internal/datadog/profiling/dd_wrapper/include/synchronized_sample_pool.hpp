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
        std::cerr << getpid() << ": deleter called, count " << tripcount << std::endl;
        ddog_ArrayQueue_drop(object);
        //  NB: the tests can fail even if I comment out the drop above.
        //*object = *object; // noop to make the compiler happy
    }
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
