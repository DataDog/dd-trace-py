#include "span_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <utility>

namespace Datadog {

void
SpanLinks::unindex_locked(const SpanLinkKey& key, uint64_t span_id)
{
    auto it = span_to_keys.find(span_id);
    if (it == span_to_keys.end()) {
        return;
    }
    it->second.erase(key);
    if (it->second.empty()) {
        span_to_keys.erase(it);
    }
}

void
SpanLinks::unindex_locked(const SpanLinkKey& key, const std::optional<Span>& span)
{
    if (span && span->kind == SpanLinkKind::Span) {
        unindex_locked(key, span->span_id);
    }
}

void
SpanLinks::remove_locked(const SpanLinkKey& key)
{
    auto it = key_to_entry.find(key);
    if (it == key_to_entry.end()) {
        return;
    }
    unindex_locked(key, it->second.span);
    key_to_entry.erase(it);
}

// Writes span to one key and keeps span_to_keys in sync with it.
void
SpanLinks::assign_locked(const SpanLinkKey& key, std::optional<Span> span)
{
    auto it = key_to_entry.find(key);
    if (it != key_to_entry.end()) {
        unindex_locked(key, it->second.span);
    }

    if (!span) {
        if (it == key_to_entry.end()) {
            // "No span" never creates a key.
            return;
        }
        if (key.is_thread()) {
            key_to_entry.erase(it);
        } else {
            // Keep a tombstone, so the key keeps its birth for the rest of the object's life.
            it->second.span.reset();
        }
        return;
    }

    // Index only the span itself, not its local root: a local root can finish before an active child, and that must
    // not move the child's link.
    if (span->kind == SpanLinkKind::Span) {
        span_to_keys[span->span_id].insert(key);
    }

    if (it != key_to_entry.end()) {
        it->second.span = std::move(span);
    } else if (key.is_thread()) {
        key_to_entry.emplace(key, SpanLinkEntry{ std::move(span), 0 });
    } else {
        const uint64_t birth = birth_counter.load(std::memory_order_relaxed) + 1;
        key_to_entry.emplace(key, SpanLinkEntry{ std::move(span), birth });
        birth_counter.store(birth, std::memory_order_release);
    }
}

bool
SpanLinks::link(uint64_t token,
                std::optional<uint64_t> thread_id,
                uint64_t task_id,
                uint64_t greenlet_id,
                const std::optional<Span>& span)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!token_is_current_locked(token)) {
        return false;
    }

    if (thread_id) {
        assign_locked({ SpanLinkDomain::Thread, *thread_id }, span);
    }
    if (task_id != 0) {
        assign_locked({ SpanLinkDomain::AsyncioTask, task_id }, span);
    }
    if (greenlet_id != 0) {
        assign_locked({ SpanLinkDomain::Greenlet, greenlet_id }, span);
    }
    return true;
}

void
SpanLinks::retarget_locked(uint64_t span_id, const std::optional<Span>& replacement)
{
    auto it = span_to_keys.find(span_id);
    if (it == span_to_keys.end()) {
        return;
    }
    // Take the keys out first, since assign_locked may index the replacement and rehash span_to_keys.
    const KeySet keys = std::move(it->second);
    span_to_keys.erase(it);
    for (const auto& key : keys) {
        assign_locked(key, replacement);
    }
}

void
SpanLinks::clear_maps_locked()
{
    key_to_entry.clear();
    span_to_keys.clear();
}

void
SpanLinks::advance_lifecycle_locked(bool open)
{
    clear_maps_locked();

    const uint64_t token = lifecycle_token.load(std::memory_order_relaxed);
    const bool is_open = (token & 1U) != 0;
    // Always advance, even when already in the requested state (open() while open moves 3 to 5), so that no token
    // handed out before this call matches again.
    const uint64_t next = token + (is_open == open ? 2 : 1);
    lifecycle_token.store(next, std::memory_order_release);
}

bool
SpanLinks::retarget(uint64_t token, uint64_t span_id, const std::optional<Span>& replacement)
{
    std::lock_guard<std::mutex> lock(mtx);
    if (!token_is_current_locked(token)) {
        return false;
    }
    retarget_locked(span_id, replacement);
    return true;
}

bool
SpanLinks::holds_span(uint64_t span_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    return span_to_keys.find(span_id) != span_to_keys.end();
}

void
SpanLinks::clear(SpanLinkKey key)
{
    std::lock_guard<std::mutex> lock(mtx);
    remove_locked(key);
}

std::optional<SpanLinkEntry>
SpanLinks::get_entry(SpanLinkKey key)
{
    std::lock_guard<std::mutex> lock(mtx);
    auto it = key_to_entry.find(key);
    if (it == key_to_entry.end()) {
        return std::nullopt;
    }
    return it->second;
}

void
SpanLinks::open()
{
    std::lock_guard<std::mutex> lock(mtx);
    advance_lifecycle_locked(true);
}

void
SpanLinks::close()
{
    std::lock_guard<std::mutex> lock(mtx);
    advance_lifecycle_locked(false);
}

void
SpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);
    assign_locked({ SpanLinkDomain::Thread, thread_id },
                  Span(span_id, local_root_span_id, std::move(span_type), SpanLinkKind::Span));
}

const std::optional<Span>
SpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = key_to_entry.find({ SpanLinkDomain::Thread, thread_id });
    if (it == key_to_entry.end()) {
        return std::nullopt;
    }
    return it->second.span;
}

void
SpanLinks::unlink_span(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    remove_locked({ SpanLinkDomain::Thread, thread_id });
}

void
SpanLinks::unlink_span(uint64_t thread_id, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    const SpanLinkKey key{ SpanLinkDomain::Thread, thread_id };
    auto it = key_to_entry.find(key);
    if (it == key_to_entry.end()) {
        return;
    }
    const std::optional<Span>& existing = it->second.span;
    if (existing.has_value() && existing->span_id == expected_span_id) {
        remove_locked(key);
    }
}

void
SpanLinks::unlink_finished_span(uint64_t span_id)
{
    std::lock_guard<std::mutex> lock(mtx);
    retarget_locked(span_id, std::nullopt);
}

void
SpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    clear_maps_locked();
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
    new (&instance.key_to_entry) KeyToEntry();
    new (&instance.span_to_keys) SpanToKeys();
    new (&instance.pending_span_links) std::unordered_map<uint64_t, PendingSpanLink>();
    // The lifecycle token and the birth counter are kept. Only the forking thread exists in the child, so no write
    // can be in flight, and keeping the birth counter keeps it monotonic for the sampler.
}

} // namespace Datadog
