#pragma once

#include "sample.hpp"

namespace Datadog {

// SampleManager provides static methods for sample lifecycle management.
// Configuration state is stored in the ProfilerState singleton.
class SampleManager
{
  public:
    // Configuration
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);
    static void set_timeline(bool enable);
    static void set_sample_pool_capacity(size_t capacity);

    // Sampling entrypoint (this could also be called `build_ptr()`)
    static Sample* start_sample();
    static void drop_sample(Sample* sample);
};

} // namespace Datadog
