#include "ddup.hpp"

#include "libdatadog_helpers.hpp"

#include <chrono>
#include <iostream>
#include <pthread.h>
#include <thread>
#include <unistd.h>

extern "C"
{
#include <datadog/profiling.h>
}

namespace Datadog {

Ddup&
Ddup::get()
{
    static Ddup instance;
    return instance;
}

bool
Ddup::init_profiles_dictionary()
{
    ddog_prof_ProfilesDictionaryHandle temp = nullptr;
    auto result = ddog_prof_ProfilesDictionary_new(&temp);
    if (result.flags) {
        std::cerr << "could not initialise profiles dictionary: " << result.err << std::endl;
        return false;
    }

    dict_handle_.store(temp, std::memory_order_release);
    return true;
}

std::optional<ddog_prof_ProfilesDictionaryHandle>
Ddup::get_profiles_dictionary()
{
    auto handle = dict_handle_.load(std::memory_order_acquire);
    if (handle == nullptr) {
        return std::nullopt;
    }
    return handle;
}

void
Ddup::release_profiles_dictionary()
{
    ddog_prof_ProfilesDictionaryHandle temp = dict_handle_.load(std::memory_order_relaxed);
    ddog_prof_ProfilesDictionary_drop(&temp);
    dict_handle_.store(nullptr, std::memory_order_release);
}

bool
Ddup::init_interned_strings()
{
    auto maybe_dict = get_profiles_dictionary();
    if (!maybe_dict) {
        return false;
    }

    // Intern the empty string, which is used frequently
    ddog_prof_StringId2 string_id;
    auto result = ddog_prof_ProfilesDictionary_insert_str(
      &string_id, maybe_dict.value(), to_slice(""), ddog_prof_Utf8Option::DDOG_PROF_UTF8_OPTION_CONVERT_LOSSY);

    if (result.flags) {
        std::cerr << "Error interning empty string: " << result.err << std::endl;
        return false;
    }
    cached_empty_string_id = string_id;

    return true;
}

void
Ddup::reset_key_caches()
{
    for (auto& entry : tag_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
    for (auto& entry : label_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
    cached_empty_string_id = nullptr;
}

void
Ddup::start()
{
    std::call_once(init_flag_, [this]() {
        // Initialize the profiles dictionary at process start
        if (!init_profiles_dictionary()) {
            return;
        }

        // Initialize cached interned strings (must happen after profiles dictionary is created)
        if (!init_interned_strings()) {
            return;
        }

        // Initialize the Profile object
        profile_state.one_time_init(type_mask, max_nframes);

        // Install fork handlers
        pthread_atfork([]() { Ddup::get().prefork(); },
                       []() { Ddup::get().postfork_parent(); },
                       []() { Ddup::get().postfork_child(); });

        // Register cleanup function to free resources on exit
        std::atexit([]() { Ddup::get().cleanup(); });

        // Set the global initialization flag
        initialized_.store(true, std::memory_order_release);
    });
}

void
Ddup::cleanup()
{
    // Clear the profile, decreasing the refcount on the Profiles Dictionary
    profile_state.cleanup();

    // Decrease the refcount on the Profiles Dictionary
    release_profiles_dictionary();
}

void
Ddup::prefork()
{
    // Cancel inflight uploads to prevent state leaking to children
    auto current_cancel = upload_cancel.exchange({ .inner = nullptr });
    if (current_cancel.inner != nullptr) {
        ddog_CancellationToken_cancel(&current_cancel);
        ddog_CancellationToken_drop(&current_cancel);
    }

    // Keep cancelling and trying to acquire the lock until we succeed
    while (!upload_lock.try_lock()) {
        current_cancel = upload_cancel.exchange({ .inner = nullptr });
        if (current_cancel.inner != nullptr) {
            ddog_CancellationToken_cancel(&current_cancel);
            ddog_CancellationToken_drop(&current_cancel);
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    // upload_lock is now held - will be released in postfork_parent/child
}

void
Ddup::postfork_parent()
{
    upload_lock.unlock();
}

void
Ddup::postfork_child()
{
    // Re-init the mutex (placement-new to avoid UB with mutex in undefined state after fork)
    new (&upload_lock) std::mutex();

    // Free our copy of the Profiles Dictionary - its String IDs refer to memory
    // that doesn't exist in the child process
    release_profiles_dictionary();

    // Reset all caches that depend on the Profiles Dictionary
    reset_key_caches();

    // Re-initialize the Profiles Dictionary in the child process
    if (!init_profiles_dictionary()) {
        std::cerr << "failed to initialise profiles dictionary in child process, profiler will be disabled"
                  << std::endl;
        initialized_.store(false, std::memory_order_release);
        return;
    }

    // Initialize cached interned strings with the new Profiles Dictionary
    if (!init_interned_strings()) {
        std::cerr << "failed to initialise interned strings in child process, profiler will be disabled" << std::endl;
        initialized_.store(false, std::memory_order_release);
        return;
    }

    // Reset the profile state
    profile_state.postfork_child();
}

} // namespace Datadog
