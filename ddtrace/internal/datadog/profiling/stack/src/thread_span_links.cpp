#include "thread_span_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <utility>

namespace Datadog {

void
ThreadSpanLinks::remove_thread_locked(uint64_t thread_id)
{
    auto thread_it = thread_id_to_span.find(thread_id);
    if (thread_it == thread_id_to_span.end()) {
        return;
    }

    const auto& span = *(thread_it->second);
    auto span_it = span_to_threads.find(span.span_id);
    if (span_it != span_to_threads.end()) {
        span_it->second.erase(thread_id);
        if (span_it->second.empty()) {
            span_to_threads.erase(span_it);
        }
    }
    thread_id_to_span.erase(thread_it);
}

void
ThreadSpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    remove_thread_locked(thread_id);
    thread_id_to_span.emplace(thread_id, std::make_unique<Span>(span_id, local_root_span_id, std::move(span_type)));
    // Index only the current span. A local root can finish before an active child, and finishing it must not remove the
    // child's attribution before that child finishes.
    span_to_threads[span_id].insert(thread_id);
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_span.find(thread_id);
    if (it == thread_id_to_span.end()) {
        return std::nullopt;
    }
    return *(it->second);
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    remove_thread_locked(thread_id);
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_span.find(thread_id);
    if (it != thread_id_to_span.end() && it->second->span_id == expected_span_id) {
        remove_thread_locked(thread_id);
    }
}

void
ThreadSpanLinks::unlink_finished_span(uint64_t span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto span_it = span_to_threads.find(span_id);
    if (span_it == span_to_threads.end()) {
        return;
    }

    for (const auto thread_id : span_it->second) {
        thread_id_to_span.erase(thread_id);
    }
    span_to_threads.erase(span_it);
}

void
ThreadSpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_span.clear();
    span_to_threads.clear();
}

void
ThreadSpanLinks::postfork_child()
{
    auto& instance = get_instance();
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&instance.mtx) std::mutex();
    // Either map may be in a mid-mutation state if fork raced with a link or unlink. Its inherited pointers may be
    // inconsistent, so calling clear (or letting the destructor run) would traverse corrupted linked-list state,
    // which is UB. Reconstruct the maps in place without inspecting their contents. This intentionally leaks the old
    // maps' heap allocations, but that memory belonged to the parent's address-space snapshot and cannot be freed
    // safely in the child.
    new (&instance.thread_id_to_span) std::unordered_map<uint64_t, std::unique_ptr<Span>>();
    new (&instance.span_to_threads) SpanToThreads();
}

} // namespace Datadog
