#pragma once

#include <mutex>

namespace Datadog {

// RAII guard that pairs a unique_lock with a reference to the protected value.
// Analogous to Rust's MutexGuard<T>: the lock is held for the lifetime of
// the Borrow, and released when it is destroyed or moved from.
template<typename T>
struct Borrow
{
    std::unique_lock<std::mutex> lock;
    T& value;
};

} // namespace Datadog
