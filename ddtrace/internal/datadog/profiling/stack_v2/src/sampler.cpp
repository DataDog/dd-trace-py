#include "sampler.hpp"

#include "echion/interp.h"
#include "echion/tasks.h"
#include "echion/threads.h"

using namespace Datadog;

void
Sampler::sampling_thread(unsigned long seq_num)
{
    using namespace std::chrono;
    auto sample_time_prev = steady_clock::now();

    while (seq_num == thread_seq_num.load()) {
        auto sample_time_now = steady_clock::now();
        auto wall_time_us = duration_cast<microseconds>(sample_time_now - sample_time_prev).count();
        sample_time_prev = sample_time_now;

        // Perform the sample
        for_each_interp([&](PyInterpreterState* interp) -> void {
            for_each_thread(interp, [&](PyThreadState* tstate, ThreadInfo& thread) {
                thread.sample(interp->id, tstate, wall_time_us);
            });
        });

        // Sleep for the remainder of the interval, get it atomically
        // Generally speaking system "sleep" times will wait _at least_ as long as the specified time, so
        // in actual fact the duration may be more than we indicated.  This tends to be more true on busy
        // systems.
        std::this_thread::sleep_until(sample_time_now + microseconds(sample_interval_us.load()));
    }
}

void
Sampler::set_interval(double new_interval_s)
{
    microsecond_t new_interval_us = static_cast<unsigned int>(new_interval_s * 1e6);
    sample_interval_us.store(new_interval_us);
}

void
Sampler::set_max_nframes(unsigned int _max_nframes)
{
    if (thread_seq_num.load() == 0) {
        // Only change this if the thread isn't running.  This is a defensive check since the
        // caller, at the moment, might only use this during init anyway.
        max_nframes = _max_nframes;
    }
}

Sampler::Sampler()
{
    renderer_ptr = std::make_shared<StackRenderer>();
}

Sampler&
Sampler::get()
{
    static Sampler instance;
    return instance;
}

void
Sampler::one_time_setup()
{
    _set_cpu(true);
    init_frame_cache(echion_frame_cache_size);
    _set_pid(getpid());

    // Register our rendering callbacks with echion's Renderer singleton
    Renderer::get().set_renderer(renderer_ptr);
}

void
Sampler::start()
{
    static std::once_flag once;
    std::call_once(once, [this]() { this->one_time_setup(); });

    // OK, now we can start the profiler.  When we launch the profiler, we mutate the sequence number and give it to the
    // thread.  This will (eventually) terminate any outstanding thread.
    std::thread t(&Sampler::sampling_thread, this, ++thread_seq_num);
    t.detach();
}

void
Sampler::stop()
{
    // Modifying the thread sequence number will cause the sampling thread to exit when it completes
    // a sampling loop.  Currently there is no mechanism to force stuck threads, should they get locked.
    ++thread_seq_num;
}
