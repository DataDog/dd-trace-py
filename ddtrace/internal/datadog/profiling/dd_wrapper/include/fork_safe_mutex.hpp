#pragma once

#include <pthread.h>

namespace Datadog {

// These locks live for the lifetime of the profiler. Only the single-threaded child replaces their storage.
class ForkSafeMutex
{
    struct Storage
    {
        pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
    };

    Storage initial;
    Storage* current = &initial;

  public:
    ForkSafeMutex() = default;
    ForkSafeMutex(const ForkSafeMutex&) = delete;
    ForkSafeMutex& operator=(const ForkSafeMutex&) = delete;

    void lock() { pthread_mutex_lock(&current->mutex); }
    void unlock() { pthread_mutex_unlock(&current->mutex); }

    void postfork_child()
    {
        // A copied mutex can have owners or waiters from the parent. Give the child a fresh address.
        Storage* inherited = current;
        current = new Storage;
        if (inherited != &initial) {
            // Reclaim storage from a previous fork without operating on its copied pthread mutex.
            delete inherited;
        }
    }
};

} // namespace Datadog
