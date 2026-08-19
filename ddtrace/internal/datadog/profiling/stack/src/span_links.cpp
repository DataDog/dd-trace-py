#include "span_links.hpp"

#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <utility>

namespace Datadog {

void
ThreadSpanLinks::remove_target_locked(const SpanLinkTarget& target)
{
    auto target_it = target_to_span.find(target);
    if (target_it == target_to_span.end()) {
        return;
    }

    const auto& span = target_it->second;
    auto span_it = span_to_targets.find(span.span_id);
    if (span_it != span_to_targets.end()) {
        span_it->second.erase(target);
        if (span_it->second.empty()) {
            span_to_targets.erase(span_it);
        }
    }
    target_to_span.erase(target_it);
}

void
ThreadSpanLinks::link_target(SpanLinkTarget target,
                             uint64_t span_id,
                             uint64_t local_root_span_id,
                             std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    remove_target_locked(target);
    Span span(span_id, local_root_span_id, std::move(span_type));
    target_to_span.try_emplace(target, std::move(span));
    // Index only the current span. A local root can finish before an active child, and finishing it must not remove the
    // child's attribution before that child finishes.
    span_to_targets[span_id].insert(target);
}

const std::optional<Span>
ThreadSpanLinks::get_active_span(const SpanLinkTarget& target)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = target_to_span.find(target);
    if (it == target_to_span.end()) {
        return std::nullopt;
    }
    return it->second;
}

void
ThreadSpanLinks::unlink_target(SpanLinkTarget target)
{
    std::lock_guard<std::mutex> lock(mtx);
    remove_target_locked(target);
}

void
ThreadSpanLinks::unlink_target(SpanLinkTarget target, uint64_t expected_span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = target_to_span.find(target);
    if (it != target_to_span.end() && it->second.span_id == expected_span_id) {
        remove_target_locked(target);
    }
}

void
ThreadSpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    link_target({ SpanLinkDomain::Thread, thread_id }, span_id, local_root_span_id, std::move(span_type));
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    return get_active_span({ SpanLinkDomain::Thread, thread_id });
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id)
{
    unlink_target({ SpanLinkDomain::Thread, thread_id });
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id, uint64_t expected_span_id)
{
    unlink_target({ SpanLinkDomain::Thread, thread_id }, expected_span_id);
}

void
ThreadSpanLinks::link_logical_span(SpanLinkDomain domain,
                                   uint64_t logical_id,
                                   uint64_t span_id,
                                   uint64_t local_root_span_id,
                                   std::string span_type)
{
    link_target({ domain, logical_id }, span_id, local_root_span_id, std::move(span_type));
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_logical_id(SpanLinkDomain domain, uint64_t logical_id)
{
    return get_active_span({ domain, logical_id });
}

void
ThreadSpanLinks::unlink_logical_span(SpanLinkDomain domain, uint64_t logical_id)
{
    unlink_target({ domain, logical_id });
}

void
ThreadSpanLinks::unlink_finished_span(uint64_t span_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto span_it = span_to_targets.find(span_id);
    if (span_it == span_to_targets.end()) {
        return;
    }

    for (const auto& target : span_it->second) {
        target_to_span.erase(target);
    }
    span_to_targets.erase(span_it);
}

void
ThreadSpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    target_to_span.clear();
    span_to_targets.clear();
}

void
ThreadSpanLinks::on_link_start(uint64_t span_id)
{
    ++pending_span_links[span_id].count;
}

bool
ThreadSpanLinks::on_link_end(uint64_t span_id)
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
ThreadSpanLinks::on_span_finish(uint64_t span_id)
{
    auto pending_span = pending_span_links.find(span_id);
    if (pending_span == pending_span_links.end()) {
        return true;
    }

    pending_span->second.finished = true;
    return false;
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
    // maps' heap allocations, because their possibly corrupted pointers cannot be safely traversed or freed in the
    // child.
    new (&instance.target_to_span) TargetToSpan();
    new (&instance.span_to_targets) SpanToTargets();
    new (&instance.pending_span_links) std::unordered_map<uint64_t, PendingSpanLink>();
}

} // namespace Datadog
