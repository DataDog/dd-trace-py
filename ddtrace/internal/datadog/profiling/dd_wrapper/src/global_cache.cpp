#include "global_cache.hpp"

#include <mutex>
#include <stdexcept>

using namespace Datadog;

ProfileGlobalStorage&
ProfileGlobalStorage::get_singleton()
{
    static ProfileGlobalStorage instance;
    return instance;
}

Profile&
ProfileGlobalStorage::get(std::thread::id id)
{
    ProfileGlobalStorage& global_store = get_singleton();

    std::unique_lock<std::mutex> lock(global_store.profile_storage_mtx);
    auto& profiles = global_store.profile_cache;
    auto it = profiles.find(id); // Attempt to find the profile once

    if (it == profiles.end()) {
        // Profile not found, unlock the mutex while creating the profile
        lock.unlock();

        auto new_profile = global_store.profile_builder.build();
        if (!new_profile) {
            throw std::runtime_error("Error creating profile");
        }

        // Lock again to insert the new profile
        lock.lock();
        auto [new_it, inserted] = profiles.emplace(
          std::piecewise_construct, std::forward_as_tuple(id), std::forward_as_tuple(std::move(new_profile.value())));
        return new_it->second;
    }

    // If we're here, it's because the profile was found, so just return it
    return it->second;
}

void
ProfileGlobalStorage::clear()
{
    // It's UB, but testing shows that on Linux, the pthreads implementation
    // backing std::mutex guarantees consistency across forks.
    ProfileGlobalStorage& global_store = get_singleton();
    std::lock_guard<std::mutex> lock(global_store.profile_storage_mtx);
    global_store.profile_cache.clear();
}
