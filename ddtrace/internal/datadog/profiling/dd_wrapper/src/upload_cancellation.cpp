#include "upload_cancellation.hpp"

#include "fork_utils.hpp"
#include <new>
#include <utility>

void
Datadog::UploadCancellation::cancel_current_unlocked()
{
    if (current.has_value()) {
        (*current)->cancel();
        current.reset();
    }
}

rust::Box<Datadog::ddprof::CancellationToken>
Datadog::UploadCancellation::start_upload()
{
    auto new_cancel = ddprof::CancellationToken::create();
    auto cancel_for_request = new_cancel->clone();

    const std::lock_guard<std::mutex> lock(mtx);
    cancel_current_unlocked();
    current = std::move(new_cancel);

    return cancel_for_request;
}

void
Datadog::UploadCancellation::cancel_inflight()
{
    const std::lock_guard<std::mutex> lock(mtx);
    cancel_current_unlocked();
}

void
Datadog::UploadCancellation::prefork()
{
    // Hold mtx across fork so another thread cannot own it in the child process.
    // postfork_parent releases it; postfork_child replaces it with a fresh mutex.
    mtx.lock();
    cancel_current_unlocked();
}

void
Datadog::UploadCancellation::postfork_parent()
{
    mtx.unlock();
}

void
Datadog::UploadCancellation::postfork_child()
{
    // Re-init the mutex after fork. The child is single-threaded here, so it can
    // safely drop the inherited token slot after replacing the inherited mutex.
    reset_mutex_after_fork(mtx);
    current.reset();
}
