#pragma once

#include <atomic>
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

// The kind of execution unit a key names. Tasks and greenlets share an OS thread, so each gets its own key. The domain
// keeps a task's address from colliding with an equal thread id.
enum class SpanLinkDomain : uint8_t
{
    Thread = 0,
    AsyncioTask = 1,
    Greenlet = 2,
};

// Whether the tracer's active item was a ddtrace Span or a Context (see ActiveTrace in ddtrace/_trace/provider.py).
enum class SpanLinkKind : uint8_t
{
    // A live span. When it finishes, retarget moves its links to whatever becomes active next.
    Span = 0,
    // A span id with no live span behind it, e.g. a context copied into a thread pool worker or from distributed
    // headers. The worker keeps parenting spans to it after the caller's span finishes, so it is never retargeted.
    Context = 1,
};

// The active item a link points at, of either kind.
struct Span
{
    uint64_t span_id;
    // The first span of this trace in this process, usually the request span.
    uint64_t local_root_span_id;
    // The local root span's type, for example "web".
    std::string span_type;
    SpanLinkKind kind;

    Span(uint64_t _span_id,
         uint64_t _local_root_span_id,
         std::string _span_type,
         SpanLinkKind _kind = SpanLinkKind::Span)
      : span_id(_span_id)
      , local_root_span_id(_local_root_span_id)
      , span_type(std::move(_span_type))
      , kind(_kind)
    {
    }

    // for testing
    bool operator==(const Span& other) const
    {
        return span_id == other.span_id && local_root_span_id == other.local_root_span_id &&
               span_type == other.span_type && kind == other.kind;
    }
};

struct SpanLinkKey
{
    SpanLinkDomain domain;
    // PyThreadState thread id for a thread, id(obj) for a task or greenlet.
    uint64_t identifier;

    bool is_thread() const { return domain == SpanLinkDomain::Thread; }

    bool operator==(const SpanLinkKey& other) const { return domain == other.domain && identifier == other.identifier; }
};

struct SpanLinkKeyHash
{
    std::size_t operator()(const SpanLinkKey& key) const
    {
        const auto domain = static_cast<std::size_t>(key.domain);
        return std::hash<uint64_t>{}(key.identifier) ^ (domain + 0x9e3779b9U + (domain << 6U) + (domain >> 2U));
    }
};

// What one execution unit is running.
struct SpanLinkEntry
{
    // Empty for a task or greenlet tombstone, which keeps its birth while the object is alive. Thread keys with no
    // span are erased instead.
    std::optional<Span> span;

    // Guards against address reuse. Each new task or greenlet key gets the next value of a global counter, kept until
    // the key is removed. The sampler reads birth_watermark() before unwinding a thread and ignores keys born after
    // it, so a task created at a freed task's address during the unwind is not attributed to the old frames. Thread
    // keys use 0.
    uint64_t birth = 0;

    bool operator==(const SpanLinkEntry& other) const { return span == other.span && birth == other.birth; }
};

// Maps each execution unit to its active span so the sampler can tag samples. Written by tracer hooks, read by the
// sampling thread without the GIL. mtx protects both maps. Python callers should release the GIL around every call.
//
// The thread-only operations (link_span, unlink_span, unlink_finished_span, reset) keep their old behavior: every
// link is kind Span and the lifecycle token is ignored. The keyed operations (link, retarget, holds_span, clear,
// get_entry, open, close) need the current token for writes.
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

    void link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);
    const std::optional<Span> get_active_span_from_thread_id(uint64_t thread_id);
    void unlink_span(uint64_t thread_id);
    void unlink_span(uint64_t thread_id, uint64_t expected_span_id);
    void unlink_finished_span(uint64_t span_id);
    void reset();

    // These lifecycle methods run with the GIL held, before or after a native map mutation that releases it.
    void on_link_start(uint64_t span_id);
    bool on_link_end(uint64_t span_id);
    bool on_span_finish(uint64_t span_id);

    // Odd while open, even while closed, advanced by every open() and close(). Writers read it before reading tracer
    // state and pass it to link or retarget, which drop the write unless it is still current and odd. This keeps a
    // write racing a profiler restart from leaking into the new run.
    uint64_t lifecycle() const { return lifecycle_token.load(std::memory_order_acquire); }

    // The birth of the most recently created task or greenlet key (see SpanLinkEntry::birth).
    uint64_t birth_watermark() const { return birth_counter.load(std::memory_order_acquire); }

    // Writes span to the thread key (if thread_id is set) and to the nonzero task and greenlet keys, atomically. An
    // empty span erases a thread key, tombstones an existing task or greenlet key, and creates no new one. Returns
    // false without writing if token is stale.
    bool link(uint64_t token,
              std::optional<uint64_t> thread_id,
              uint64_t task_id,
              uint64_t greenlet_id,
              const std::optional<Span>& span);

    // Called when span_id finishes. Points its Span-kind links at replacement (usually the parent), with the same
    // empty-span rule as link. Idempotent. Returns false without writing if token is stale.
    bool retarget(uint64_t token, uint64_t span_id, const std::optional<Span>& replacement);

    // Whether any Span-kind link still holds span_id.
    bool holds_span(uint64_t span_id);

    // Removes one key, tombstone included, e.g. when its task is freed. Needs no token since removal can't go stale.
    void clear(SpanLinkKey key);

    std::optional<SpanLinkEntry> get_entry(SpanLinkKey key);

    // Clear every key and advance the token (odd for open, even for close) under one hold of the mutex.
    void open();
    void close();

    // Resets the mutex and maps but keeps the lifecycle token and the birth counter.
    static void postfork_child();

  private:
    using KeySet = std::unordered_set<SpanLinkKey, SpanLinkKeyHash>;
    using KeyToEntry = std::unordered_map<SpanLinkKey, SpanLinkEntry, SpanLinkKeyHash>;
    using SpanToKeys = std::unordered_map<uint64_t, KeySet>;

    struct PendingSpanLink
    {
        size_t count = 0;
        bool finished = false;
    };

    bool token_is_current_locked(uint64_t token) const
    {
        return (token & 1U) != 0 && token == lifecycle_token.load(std::memory_order_relaxed);
    }

    void assign_locked(const SpanLinkKey& key, std::optional<Span> span);
    void retarget_locked(uint64_t span_id, const std::optional<Span>& replacement);
    void remove_locked(const SpanLinkKey& key);
    void unindex_locked(const SpanLinkKey& key, uint64_t span_id);
    void unindex_locked(const SpanLinkKey& key, const std::optional<Span>& span);
    void clear_maps_locked();
    void advance_lifecycle_locked(bool open);

    std::mutex mtx;
    KeyToEntry key_to_entry;
    // Span-kind keys by span id, for retarget. Context-kind entries are never retargeted, so they are left out.
    SpanToKeys span_to_keys;

    // Written under mtx. Read without it by lifecycle() and birth_watermark().
    std::atomic<uint64_t> lifecycle_token{ 0 };
    std::atomic<uint64_t> birth_counter{ 0 };

    // Protected by the GIL. This bridges lifecycle callback order to mutations that release it above.
    std::unordered_map<uint64_t, PendingSpanLink> pending_span_links;

    // Private Constructor/Destructor
    SpanLinks() = default;
    ~SpanLinks() = default;
};

}
