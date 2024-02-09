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

    // Tries to check out the requested handle, clearing its storage if successful
    static Sample *start_sample();

    // Only post-fork (child)
    static void handles_release();
    static void postfork_child();

    // flush_sample is special, since it frees/invalidates the sample.  After this point,
    // the sample is no longer valid.
    template<typename... Args>
    static inline bool flush_sample(Sample *sample, Args&&... args)
    {
        auto ret = sample->flush_sample(std::forward<Args>(args)...);
        delete sample;
        return ret;
    }

    void drop_sample(Sample *sample);
};

// Prevent leaking the macro
#undef MAKE_PROXY

} // namespace Datadog
