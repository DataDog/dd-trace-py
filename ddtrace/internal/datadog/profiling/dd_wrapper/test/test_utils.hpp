#include "ddup_interface.hpp"

#include <array>
#include <atomic>
#include <iostream>
#include <random>
#include <string_view>
#include <thread>
#include <vector>

static constexpr std::array<std::array<std::string_view, 7>, 3> names = { {
  {
    "Unreliable",
    "Dastardly",
    "Careless",
    "Clever",
    "Inexpensive",
    "Righteous",
    "Wonderful",
  },
  {
    "IBM",
    "DEC",
    "HPE",
    "Fujitsu",
    "Cray",
    "NEC",
    "SGI",
  },
  {
    "Buyer",
    "Seller",
    "Trader",
    "Broker",
    "Dealer",
    "Merchant",
    "Customer",
  },
} };

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
    ddup_start();
}

inline static std::string
get_name()
{
    constexpr auto sz = names[0].size();

    thread_local static std::random_device rd;
    thread_local static std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(0, sz - 1);

    auto id1 = dis(gen);
    auto id2 = dis(gen);
    auto id3 = dis(gen);

    std::string name;
    name.reserve(30);
    name += names[0][id1];
    name += "_";
    name += names[1][id2];
    name += "_";
    name += names[2][id3];

    return name;
}

// This is a generic function for sending a small, fake sample to the profiler
// on a given id.  Its only purpose is to mock a single sampling operation.
void
send_sample(unsigned int id)
{
    auto h = ddup_start_sample();
    thread_local static std::random_device rd;
    thread_local static std::mt19937 gen(rd());

    // If the sample is not a valid ID, then randomly choose one
    if (id < 1 || id > 4) {
        std::uniform_int_distribution<> dis(1, 4);
        id = dis(gen);
    }

    // Use ID to determine what kind of sample to send
    switch (id) {
        case 1: // stack
            ddup_push_walltime(h, 1.0, 1);
            ddup_push_cputime(h, 1.0, 1);
            ddup_push_exceptioninfo(h, get_name().c_str(), 1);
            break;
        case 2: // lock
            ddup_push_acquire(h, 1.0, 1);
            ddup_push_lock_name(h, get_name().c_str());
            break;
        case 3: // memory
            ddup_push_alloc(h, 1.0, 1);
            break;
        case 4: // heap
            ddup_push_heap(h, 1);
            break;
    }

    for (int i = 0; i < 16; i++) {
        std::string file = "site-packages/" + get_name() + "/file_" + std::to_string(i) + ".py";
        std::string func = get_name() + std::to_string(i);
        ddup_push_frame(h, func.c_str(), file.c_str(), i, 0);
    }
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;
}

// This emulates a single sampler, which periodically (100hz) sends a sample
// Note that this is unlike the behavior of real samplers, in general, but whatever
// The done flag is used to signal the sampler to stop
void
emulate_sampler(unsigned int id, unsigned int sleep_time_ns, std::atomic<bool>& done)
{
    // Sends a sample at 100hz
    while (true) {
        send_sample(id);
        std::this_thread::sleep_for(std::chrono::nanoseconds(sleep_time_ns));
        if (done.load())
            break;
    }
}

// Launches the specified number of threads, each of which emulates a different sampler
// The done flag is used to signal the samplers to stop
void
launch_samplers(std::vector<unsigned int>& ids,
                unsigned int sleep_time_ns,
                std::vector<std::thread>& threads,
                std::atomic<bool>& done)
{
    threads.clear();
    for (auto id : ids) {
        threads.push_back(std::thread(emulate_sampler, id, sleep_time_ns, std::ref(done)));
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
