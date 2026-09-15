#pragma once

#include "libdatadog_helpers.hpp"

#include <mutex>
#include <optional>

namespace Datadog {

// Owns the cancellation token for the current in-flight upload.
// The CXX cancellation token is move-only, so replacing/cancelling the current
// token requires a small synchronized slot rather than the old C FFI exchange pattern.
class UploadCancellation
{
  private:
    std::mutex mtx{};
    std::optional<rust::Box<ddprof::CancellationToken>> current{};

    void cancel_current_unlocked();

  public:
    rust::Box<ddprof::CancellationToken> start_upload();
    void cancel_inflight();
    void prefork();
    void postfork_parent();
    void postfork_child();
};

} // namespace Datadog
