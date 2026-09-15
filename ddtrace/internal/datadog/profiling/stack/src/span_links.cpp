#include "span_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <utility>

namespace Datadog {

void
SpanLinks::remove_locked(const Key& key)
{
    auto key_it = key_to_span.find(key);
    if (key_it == key_to_span.end()) {
        return;
    }

    const auto& span = key_it->second;
    auto span_it = span_to_keys.find(span.span_id);
    if (span_it != span_to_keys.end()) {
        span_it->second.erase(key);
        if (span_it->second.empty()) {
            span_to_keys.erase(span_it);
        }
    }
    key_to_span.erase(key_it);
}

void
SpanLinks::link(Key key, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    remove_locked(key);
    Span span(span_id, local_root_span_id, std::move(span_type));
    key_to_span.try_emplace(key, std::move(span));
    // Index only the current span. A local root can finish before an active child, and finishing it must not remove the
    // child's attribution before that child finishes.
    span_to_keys[span_id].insert(key);
}

const SpanAttribution
SpanLinks::get_active_span(const Key& key)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = key_to_span.find(key);
    if (it == key_to_span.end()) {
        return std::nullopt;
    }
    return it->second;
}

void
SpanLinks::unlink(Key key)
{
    std::lock_guard<std::mutex> lock(mtx);
    remove_locked(key);
}

void
SpanLinks::unlink(Key key, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = key_to_span.find(key);
    if (it != key_to_span.end() && it->second.span_id == expected_span_id) {
        remove_locked(key);
    }
}

void
SpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    link({ SpanLinkDomain::Thread, thread_id }, span_id, local_root_span_id, std::move(span_type));
}

const SpanAttribution
SpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    return get_active_span({ SpanLinkDomain::Thread, thread_id });
}

void
SpanLinks::unlink_span(uint64_t thread_id)
{
    unlink({ SpanLinkDomain::Thread, thread_id });
}

void
SpanLinks::unlink_span(uint64_t thread_id, uint64_t expected_span_id)
{
    unlink({ SpanLinkDomain::Thread, thread_id }, expected_span_id);
}

void
SpanLinks::link_logical_span(SpanLinkDomain domain,
                             uint64_t logical_id,
                             uint64_t span_id,
                             uint64_t local_root_span_id,
                             std::string span_type)
{
    link({ domain, logical_id }, span_id, local_root_span_id, std::move(span_type));
}

const SpanAttribution
SpanLinks::get_active_span_from_logical_id(SpanLinkDomain domain, uint64_t logical_id)
{
    return get_active_span({ domain, logical_id });
}

void
SpanLinks::unlink_logical_span(SpanLinkDomain domain, uint64_t logical_id)
{
    unlink({ domain, logical_id });
}

void
SpanLinks::unlink_finished_span(uint64_t span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto span_it = span_to_keys.find(span_id);
    if (span_it == span_to_keys.end()) {
        return;
    }

    for (const auto& key : span_it->second) {
        key_to_span.erase(key);
    }
    span_to_keys.erase(span_it);
}

void
SpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    key_to_span.clear();
    span_to_keys.clear();
}

void
SpanLinks::on_link_start(uint64_t span_id)
{
    ++pending_span_links[span_id].count;
}

bool
SpanLinks::on_link_end(uint64_t span_id)
{
    auto pending_span = pending_span_links.find(span_id);
    if (--pending_span->second.count != 0) {
        return false;
    }

    const bool finished = pending_span->second.finished;
    pending_span_links.erase(pending_span);
    return finished;
}

bool
SpanLinks::on_span_finish(uint64_t span_id)
{
    auto pending_span = pending_span_links.find(span_id);
    if (pending_span == pending_span_links.end()) {
        return true;
    }

    pending_span->second.finished = true;
    return false;
}

void
SpanLinks::postfork_child()
{
    auto& instance = get_instance();
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&instance.mtx) std::mutex();
    // Either map may be in a mid-mutation state if fork raced with a link or unlink. Its inherited pointers may be
    // inconsistent, so calling clear (or letting the destructor run) would traverse corrupted linked-list state,
    // which is UB. Reconstruct the maps in place without inspecting their contents. This intentionally leaks the old
    // maps' heap allocations, because their possibly corrupted pointers cannot be safely traversed or freed in the
    // child.
    new (&instance.key_to_span) KeyToSpan();
    new (&instance.span_to_keys) SpanToKeys();
    new (&instance.pending_span_links) std::unordered_map<uint64_t, PendingSpanLink>();
}

} // namespace Datadog
