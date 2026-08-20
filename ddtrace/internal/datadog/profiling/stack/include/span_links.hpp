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

    bool operator==(const Span& other) const
    {
        return span_id == other.span_id && local_root_span_id == other.local_root_span_id &&
               span_type == other.span_type;
    }
};

using SpanAttribution = std::optional<Span>;

struct TaskSpanContext
{
    // When true, span is authoritative even when empty. An empty value means the task is unattributed and suppresses
    // thread fallback.
    bool use_task_attribution = false;
    SpanAttribution span;
};

class SpanLinks
{
  public:
    static SpanLinks& get_instance()
    {
        static SpanLinks instance;
        return instance;
    }

    SpanLinks(SpanLinks const&) = delete;
    SpanLinks& operator=(SpanLinks const&) = delete;

    void link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    const SpanAttribution get_active_span_from_thread_id(uint64_t thread_id);
    void unlink_span(uint64_t thread_id);
    void unlink_span(uint64_t thread_id, uint64_t expected_span_id);

    void link_logical_span(SpanLinkDomain domain,
                           uint64_t logical_id,
                           uint64_t span_id,
                           uint64_t local_root_span_id,
                           std::string span_type);
    const SpanAttribution get_active_span_from_logical_id(SpanLinkDomain domain, uint64_t logical_id);
    void unlink_logical_span(SpanLinkDomain domain, uint64_t logical_id);

    void unlink_finished_span(uint64_t span_id);
    void reset();

    // These lifecycle methods run with the GIL held, before or after a native map mutation that releases it.
    void on_link_start(uint64_t span_id);
    bool on_link_end(uint64_t span_id);
    bool on_span_finish(uint64_t span_id);

    static void postfork_child();

  private:
    struct Key
    {
        SpanLinkDomain domain;
        uint64_t identifier;

        bool operator==(const Key& other) const { return domain == other.domain && identifier == other.identifier; }
    };

    struct KeyHash
    {
        std::size_t operator()(const Key& key) const
        {
            const auto domain = static_cast<std::size_t>(key.domain);
            return std::hash<uint64_t>{}(key.identifier) ^ (domain + 0x9e3779b9U + (domain << 6U) + (domain >> 2U));
        }
    };

    using KeySet = std::unordered_set<Key, KeyHash>;
    using KeyToSpan = std::unordered_map<Key, Span, KeyHash>;
    using SpanToKeys = std::unordered_map<uint64_t, KeySet>;

    struct PendingSpanLink
    {
        size_t count = 0;
        bool finished = false;
    };

    void link(Key key, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    void unlink(Key key);
    void unlink(Key key, uint64_t expected_span_id);
    void remove_locked(const Key& key);
    const SpanAttribution get_active_span(const Key& key);

    std::mutex mtx;
    KeyToSpan key_to_span;
    SpanToKeys span_to_keys;

    // Protected by the GIL. This bridges lifecycle callback order to mutations that release it above.
    std::unordered_map<uint64_t, PendingSpanLink> pending_span_links;

    // Private Constructor/Destructor
    SpanLinks() = default;
    ~SpanLinks() = default;
};

} // namespace Datadog
