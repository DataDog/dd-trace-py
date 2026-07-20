#include "thread_span_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>

namespace Datadog {
void
ThreadSpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_span.find(thread_id);
    if (it == thread_id_to_span.end()) {
        thread_id_to_span[thread_id] = std::make_unique<Span>(span_id, local_root_span_id, std::move(span_type));
    } else {
        it->second->span_id = span_id;
        it->second->local_root_span_id = local_root_span_id;
        it->second->span_type = std::move(span_type);
    }
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    std::optional<Span> span;
    auto it = thread_id_to_span.find(thread_id);
    if (it != thread_id_to_span.end()) {
        span = *(it->second);
    }
    return span;
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    thread_id_to_span.erase(thread_id); // This is a no-op if the key is not found
}

void
ThreadSpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_span.clear();
}

void
ThreadSpanLinks::postfork_child()
{
    auto& instance = get_instance();
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&instance.mtx) std::mutex();
    // thread_id_to_span may be in a mid-mutation state if fork raced with
    // link_span/unlink_span. Its inherited pointers may be inconsistent,
    // so calling clear (or letting the destructor run) would traverse the same
    // corrupted linked-list state, which is UB. Instead, reconstruct the map in
    // place with placement-new without inspecting its contents. This intentionally
    // leaks the old map's heap allocations (nodes/buckets), but that memory belonged
    // to the parent's address space snapshot and is a bounded, one-time leak per
    // fork in the child; freeing it safely is impossible given the possible
    // corruption, so leaking is the correct trade-off.
    new (&instance.thread_id_to_span) std::unordered_map<uint64_t, std::unique_ptr<Span>>();
}

} // namespace Datadog
