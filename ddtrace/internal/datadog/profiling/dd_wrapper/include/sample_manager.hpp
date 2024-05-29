#pragma once

#include "constants.hpp"
#include "sample.hpp"
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
    static inline timeline_enabled{ false };

  public:
    // Configuration
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);
    static void set_timeline_enabled(bool _timeline_enabled);

    // Sampling entrypoint (this could also be called `build_ptr()`)
    static Sample* start_sample();
    static void drop_sample(Sample* sample);

    // Handles state management after forks
    static void postfork_child();

    // Initialization
    static void init();
};
} // namespace Datadog
