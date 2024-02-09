#include "interface.hpp"

#include <atomic>
#include <iostream>
#include <thread>
#include <vector>

inline static void
configure(const char* service,
          const char* env,
          const char* version,
          const char* url,
          const char* runtime,
          const char* runtime_version,
          const char* profiler_version,
          int max_nframes)
{
    ddup_config_service(service);
    ddup_config_env(env);
    ddup_config_version(version);
    ddup_config_url(url);
    ddup_config_runtime(runtime);
    ddup_config_runtime_version(runtime_version);
    ddup_config_profiler_version(profiler_version);
    ddup_config_max_nframes(max_nframes);
    ddup_init();
}

// This is a generic function for sending a small, fake sample to the profiler
// on a given id.  Its only purpose is to mock a single sampling operation.
void
send_sample(unsigned int id)
{
    auto h = ddup_start_sample();

    // Use ID to determine what kind of sample to send
    switch (id) {
        case 1: // stack
            ddup_push_walltime(h, 1.0, 1);
            ddup_push_cputime(h, 1.0, 1);
            ddup_push_exceptioninfo(h, "BadException", 1);
            break;
        case 2: // lock
            ddup_push_acquire(h, 1.0, 1);
            ddup_push_lock_name(h, "GoodLock");
            break;
        case 3: // memory
            ddup_push_alloc(h, 1.0, 1);
            break;
        case 4: // heap
            ddup_push_heap(h, 1);
            break;
    }

    for (int i = 0; i < 16; i++) {
        std::string file = "file_" + std::to_string(i);
        std::string func = "function_" + std::to_string(i);
        ddup_push_frame(h, file.c_str(), func.c_str(), i, 0);
    }
    ddup_flush_sample(h);
}

// This emulates a single sampler, which periodically (100hz) sends a sample
// Note that this is unlike the behavior of real samplers, in general, but whatever
// The done flag is used to signal the sampler to stop
void
emulate_sampler(unsigned int id, std::atomic<bool>& done)
{
    // Sends a sample at 100hz
    while (true) {
        send_sample(id);
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (done.load())
            break;
    }
}

// Launches the specified number of threads, each of which emulates a different sampler
// The done flag is used to signal the samplers to stop
void
launch_samplers(std::vector<unsigned int>& ids, std::vector<std::thread>& threads, std::atomic<bool>& done)
{
    threads.clear();
    for (auto id : ids) {
        threads.push_back(std::thread(emulate_sampler, id, std::ref(done)));
    }
}

void
join_samplers(std::vector<std::thread>& threads, std::atomic<bool>& done)
{
    done.store(true);
    for (auto& t : threads) {
        t.join();
    }
}
