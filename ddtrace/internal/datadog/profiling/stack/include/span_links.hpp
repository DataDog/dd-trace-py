#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace Datadog {

enum class SpanLinkDomain : uint8_t
{
    Thread = 0,
    AsyncioTask = 1,
    GeventGreenlet = 2,
};

struct SpanLinkTarget
{
    SpanLinkDomain domain;
    uint64_t identifier;

    bool operator==(const SpanLinkTarget& other) const
    {
        return domain == other.domain && identifier == other.identifier;
    }
};

// C++20 does not provide std::hash for pairs or aggregate key types.
struct SpanLinkTargetHash
{
    std::size_t operator()(const SpanLinkTarget& target) const
    {
        const auto domain = static_cast<std::size_t>(target.domain);
        return std::hash<uint64_t>{}(target.identifier) ^ (domain + 0x9e3779b9U + (domain << 6U) + (domain >> 2U));
    }
};

struct Span
{
    uint64_t span_id;
    uint64_t local_root_span_id;
    std::string span_type;

    Span(uint64_t _span_id, uint64_t _local_root_span_id, std::string _span_type)
      : span_id(_span_id)
      , local_root_span_id(_local_root_span_id)
      , span_type(std::move(_span_type))
    {
    }

    // for testing
    bool operator==(const Span& other) const
    {
        return span_id == other.span_id && local_root_span_id == other.local_root_span_id &&
               span_type == other.span_type;
    }
};

using LogicalSpanContext = std::optional<std::optional<Span>>;

class ThreadSpanLinks
{
  public:
    static ThreadSpanLinks& get_instance()
    {
        static ThreadSpanLinks instance;
        return instance;
    }

    // Delete Copy constructor and assignment operator to prevent copies
    ThreadSpanLinks(ThreadSpanLinks const&) = delete;
    ThreadSpanLinks& operator=(ThreadSpanLinks const&) = delete;

    void link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    const std::optional<Span> get_active_span_from_thread_id(uint64_t thread_id);
    void unlink_span(uint64_t thread_id);
    void unlink_span(uint64_t thread_id, uint64_t expected_span_id);

    void link_logical_span(SpanLinkDomain domain,
                           uint64_t logical_id,
                           uint64_t span_id,
                           uint64_t local_root_span_id,
                           std::string span_type);
    const std::optional<Span> get_active_span_from_logical_id(SpanLinkDomain domain, uint64_t logical_id);
    void unlink_logical_span(SpanLinkDomain domain, uint64_t logical_id);

    void unlink_finished_span(uint64_t span_id);
    void reset();

    // These lifecycle methods run with the GIL held, before or after a native map mutation that releases it.
    void on_link_start(uint64_t span_id);
    bool on_link_end(uint64_t span_id);
    bool on_span_finish(uint64_t span_id);

    static void postfork_child();

  private:
    using TargetSet = std::unordered_set<SpanLinkTarget, SpanLinkTargetHash>;
    using TargetToSpan = std::unordered_map<SpanLinkTarget, Span, SpanLinkTargetHash>;
    using SpanToTargets = std::unordered_map<uint64_t, TargetSet>;

    struct PendingSpanLink
    {
        size_t count = 0;
        bool finished = false;
    };

    void link_target(SpanLinkTarget target, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    void unlink_target(SpanLinkTarget target);
    void unlink_target(SpanLinkTarget target, uint64_t expected_span_id);
    void remove_target_locked(const SpanLinkTarget& target);
    const std::optional<Span> get_active_span(const SpanLinkTarget& target);

    std::mutex mtx;
    TargetToSpan target_to_span;
    SpanToTargets span_to_targets;

    // Protected by the GIL. This bridges lifecycle callback order to mutations that release it above.
    std::unordered_map<uint64_t, PendingSpanLink> pending_span_links;

    // Private Constructor/Destructor
    ThreadSpanLinks() = default;
    ~ThreadSpanLinks() = default;
};

} // namespace Datadog
