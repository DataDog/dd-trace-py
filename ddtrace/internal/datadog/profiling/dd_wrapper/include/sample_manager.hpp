#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <array>
#include <atomic>
#include <mutex>
#include <optional>
#
#include <vector>

namespace Datadog {

const unsigned int g_default_nframes = 64; // TODO is this the actual default?

class SampleManager
{
  private:
    static inline unsigned int max_nframes{ g_default_nframes };
    static inline SampleType type_mask{ SampleType::All };
    static inline std::mutex init_mutex{};

  public:
    // Configuration
    static void add_type(SampleType type);
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);

    // Sampling entrypoint (this could also be called `build_ptr()`)
    static Sample *start_sample();

    // Handles state management after forks
    static void postfork_child();

    // Initialization
    static void init();
};

// Prevent leaking the macro
#undef MAKE_PROXY

} // namespace Datadog
