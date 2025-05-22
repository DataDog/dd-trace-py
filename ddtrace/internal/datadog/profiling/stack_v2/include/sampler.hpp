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

    // This is a singleton, so no public constructor
    Sampler();

    // One-time setup of echion
    void one_time_setup();

    // Internal perf counters
    uint64_t process_count = 0;
    uint64_t sampler_thread_count = 0;

    bool do_adaptive_sampling = true;
    void adapt_sampling_interval();

  public:
    // Singleton instance
    static Sampler& get();
    bool start();
    void stop();
    void register_thread(uint64_t id, uint64_t native_id, const char* name);
    void unregister_thread(uint64_t id);
    void track_asyncio_loop(uintptr_t thread_id, PyObject* loop);
    void init_asyncio(PyObject* _asyncio_current_tasks,
                      PyObject* _asyncio_scheduled_tasks,
                      PyObject* _asyncio_eager_tasks);
    void link_tasks(PyObject* parent, PyObject* child);
    void sampling_thread(const uint64_t seq_num);

    // The Python side dynamically adjusts the sampling rate based on overhead, so we need to be able to update our
    // own intervals accordingly.  Rather than a preemptive measure, we assume the rate is ~fairly stable and just
    // update the next rate with the latest interval. This is not perfect because the adjustment is based on
    // self-time, and we're not currently accounting for the echion self-time.
    void set_interval(double new_interval);
    void set_adaptive_sampling(bool value) { do_adaptive_sampling = value; }
};

} // namespace Datadog
