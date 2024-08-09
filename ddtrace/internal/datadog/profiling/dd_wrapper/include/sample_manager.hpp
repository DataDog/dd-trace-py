#pragma once

#include "constants.hpp"
#include "sample.hpp"
#include "synchronized_sample_pool.hpp"
#include "types.hpp"

#include <array>
#include <atomic>
#include <mutex>
#include <optional>

#include <vector>

namespace Datadog {

class SampleManager
{
  private:
    static inline unsigned int max_nframes{ g_default_max_nframes };
    static inline SampleType type_mask{ SampleType::All };
    static inline std::mutex init_mutex{};
    static inline size_t sample_pool_capacity{ g_default_sample_pool_capacity };
    static inline std::unique_ptr<SynchronizedSamplePool> sample_pool{ nullptr };

  public:
    // Configuration
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);
    static void set_timeline(bool enable);
    static void set_sample_pool_capacity(size_t capacity);

    // Sampling entrypoint (this could also be called `build_ptr()`)
    static Sample* start_sample();
    static void drop_sample(Sample* sample);

    // Handles state management after forks
    static void postfork_child();

    // Initialization
    static void init();
};
} // namespace Datadog
