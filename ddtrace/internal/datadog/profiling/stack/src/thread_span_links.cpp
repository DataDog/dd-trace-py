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
ThreadSpanLinks::unlink_span(uint64_t thread_id, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_span.find(thread_id);
    if (it != thread_id_to_span.end() && it->second->span_id == expected_span_id) {
        thread_id_to_span.erase(it);
    }
}

void
ThreadSpanLinks::link_logical_span(uint64_t logical_id,
                                   uint64_t span_id,
                                   uint64_t local_root_span_id,
                                   std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = logical_id_to_span.find(logical_id);
    if (it == logical_id_to_span.end()) {
        logical_id_to_span[logical_id] = std::make_unique<Span>(span_id, local_root_span_id, std::move(span_type));
    } else {
        it->second->span_id = span_id;
        it->second->local_root_span_id = local_root_span_id;
        it->second->span_type = std::move(span_type);
    }
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_logical_id(uint64_t logical_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = logical_id_to_span.find(logical_id);
    if (it == logical_id_to_span.end()) {
        return std::nullopt;
    }
    return *(it->second);
}

void
ThreadSpanLinks::unlink_logical_span(uint64_t logical_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    logical_id_to_span.erase(logical_id);
}

void
ThreadSpanLinks::unlink_logical_span(uint64_t logical_id, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = logical_id_to_span.find(logical_id);
    if (it != logical_id_to_span.end() && it->second->span_id == expected_span_id) {
        logical_id_to_span.erase(it);
    }
}

void
ThreadSpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_span.clear();
    logical_id_to_span.clear();
}

void
ThreadSpanLinks::postfork_child()
{
    auto& instance = get_instance();
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&instance.mtx) std::mutex();
    // Either span map may be in a mid-mutation state if fork raced with a link or unlink. Its inherited pointers may be
    // inconsistent, so calling clear (or letting the destructor run) would traverse the same corrupted linked-list
    // state, which is UB. Instead, reconstruct the maps in place without inspecting their contents. This intentionally
    // leaks the old maps' heap allocations, but that memory belonged to the parent's address-space snapshot and is a
    // bounded, one-time leak per fork in the child.
    new (&instance.thread_id_to_span) std::unordered_map<uint64_t, std::unique_ptr<Span>>();
    new (&instance.logical_id_to_span) std::unordered_map<uint64_t, std::unique_ptr<Span>>();
}

} // namespace Datadog
