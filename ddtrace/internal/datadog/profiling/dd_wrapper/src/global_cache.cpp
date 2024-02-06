#include "global_cache.hpp"

#include <mutex>
#include <stdexcept>

using namespace Datadog;

GlobalCache&
GlobalCache::get_singleton()
{
    static GlobalCache instance;
    return instance;
}

Sample&
GlobalCache::get(std::thread::id id)
{
    GlobalCache& global_store = get_singleton();

    std::unique_lock<std::mutex> lock(global_store.sample_storage_mtx);
    auto& samples = global_store.sample_cache;
    auto it = samples.find(id); // Attempt to find the sample once

    if (it == samples.end()) {
        // Sample not found, unlock the mutex while creating the sample
        lock.unlock();

        auto new_sample = global_store.sample_builder.build();
        if (!new_sample) {
            throw std::runtime_error("Error creating sample");
        }

        // Lock again to insert the new sample
        lock.lock();
        auto [new_it, inserted] = samples.emplace(
          std::piecewise_construct, std::forward_as_tuple(id), std::forward_as_tuple(std::move(new_sample.value())));
        return new_it->second;
    }

    // If we're here, it's because the sample was found, so just return it
    return it->second;
}

void
GlobalCache::clear()
{
    // It's UB, but testing shows that on Linux, the pthreads implementation
    // backing std::mutex guarantees consistency across forks.
    GlobalCache& global_store = get_singleton();
    std::lock_guard<std::mutex> lock(global_store.sample_storage_mtx);
    global_store.sample_cache.clear();
}
