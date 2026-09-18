#pragma once

#include "libdatadog_helpers.hpp"

#include <mutex>
#include <optional>

namespace Datadog {

// Synchronized slot for the current in-flight upload's cancellation token.
// start_upload() replaces the token and returns a clone for the request;
// cancel_inflight() cancels and drops it.
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
