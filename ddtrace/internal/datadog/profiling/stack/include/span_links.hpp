#pragma once

#include <cstddef>
#include <functional>
#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace Datadog {

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

// The kind of execution unit a link belongs to. Many asyncio tasks or gevent greenlets can run on one thread, so each
// needs its own key. The domain keeps a task's or greenlet's address from colliding with an equal thread id.
enum class SpanLinkDomain : uint8_t
{
    Thread = 0,
    AsyncioTask = 1,
    Greenlet = 2,
};

// Names one execution unit. For a thread, identifier is its PyThreadState thread id. For an asyncio task or a greenlet,
// it is id() of the object.
struct SpanLinkKey
{
    SpanLinkDomain domain;
    uint64_t identifier;

    static SpanLinkKey thread(uint64_t thread_id) { return SpanLinkKey{ SpanLinkDomain::Thread, thread_id }; }

    bool operator==(const SpanLinkKey& other) const { return domain == other.domain && identifier == other.identifier; }
};

struct SpanLinkKeyHash
{
    std::size_t operator()(const SpanLinkKey& key) const
    {
        return std::hash<uint64_t>{}(key.identifier) ^ (static_cast<std::size_t>(key.domain) << 1U);
    }
};

class SpanLinks
{
  public:
    static SpanLinks& get_instance()
    {
        // uWSGI may call exit without running the atexit hooks that stop the sampler.
        // Making this a raw pointer prevents crashes at exit.
        static SpanLinks* const instance = new SpanLinks();
        return *instance;
    }

    // Delete Copy constructor and assignment operator to prevent copies
    SpanLinks(SpanLinks const&) = delete;
    SpanLinks& operator=(SpanLinks const&) = delete;

    void link_span(SpanLinkKey key, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    const std::optional<Span> get_active_span(SpanLinkKey key);
    void unlink_span(SpanLinkKey key);
    void unlink_span(SpanLinkKey key, uint64_t expected_span_id);
    void unlink_finished_span(uint64_t span_id);
    void reset();

    // These lifecycle methods run with the GIL held, before or after a native map mutation that releases it.
    void on_link_start(uint64_t span_id);
    bool on_link_end(uint64_t span_id);
    bool on_span_finish(uint64_t span_id);

    static void postfork_child();

  private:
    using KeySet = std::unordered_set<SpanLinkKey, SpanLinkKeyHash>;
    using KeyToSpanMap = std::unordered_map<SpanLinkKey, Span, SpanLinkKeyHash>;
    using SpanToKeysMap = std::unordered_map<uint64_t, KeySet>;

    struct PendingSpanLink
    {
        size_t count = 0;
        bool finished = false;
    };

    void remove_key_locked(SpanLinkKey key);

    std::mutex mtx;
    KeyToSpanMap key_to_span;
    SpanToKeysMap span_to_keys;

    // Protected by the GIL. This bridges lifecycle callback order to mutations that release it above.
    std::unordered_map<uint64_t, PendingSpanLink> pending_span_links;

    // Private Constructor/Destructor
    SpanLinks() = default;
    ~SpanLinks() = default;
};

}
