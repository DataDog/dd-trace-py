#pragma once
#include "constants.hpp"
#include "stack_renderer.hpp"

#include <atomic>

namespace Datadog {

class Sampler
{
    // This class manages the initialization of echion as well as the sampling thread.
    // The underlying echion instance it manages keeps much of its state globally, so this class is a singleton in order
    // to keep it aligned with the echion state.
  private:
    std::shared_ptr<StackRenderer> renderer_ptr;

    // The sampling interval is atomic because it needs to be safely propagated to the sampling thread
    std::atomic<microsecond_t> sample_interval_us{ g_default_sampling_period_us };

    // This is not a running total of the number of launched threads; it is a sequence for the
    // transactions upon the sampling threads (usually starts + stops). This allows threads to be
    // stopped or started in a straightforward manner without finer-grained control (locks)
    std::atomic<uint64_t> thread_seq_num{ 0 };

    // Parameters
    uint64_t echion_frame_cache_size = g_default_echion_frame_cache_size;

    // Helper function; implementation of the echion sampling thread
    void sampling_thread(const uint64_t seq_num);

    // This is a singleton, so no public constructor
    Sampler();

    // One-time setup of echion
    void one_time_setup();

  public:
    // Singleton instance
    static Sampler& get();
    void start();
    void stop();

    // The Python side dynamically adjusts the sampling rate based on overhead, so we need to be able to update our own
    // intervals accordingly.  Rather than a preemptive measure, we assume the rate is ~fairly stable and just update
    // the next rate with the latest interval. This is not perfect because the adjustment is based on self-time, and
    // we're not currently accounting for the echion self-time.
    void set_interval(double new_interval);
};

} // namespace Datadog
