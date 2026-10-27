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

// The execution unit a span link belongs to. Thread keys use the PyThreadState thread id. Logical keys use the
// object's address (id(task) or id(greenlet)). Keeping the domain in the key stops an address from colliding with a
// thread id.
enum class SpanLinkDomain : uint8_t
{
    Thread = 0,
    AsyncioTask = 1,
    Greenlet = 2,
};

// What the tracer had active when the link was written. Only Span links follow a span finish. When the active item
// is a Context, the tracer keeps parenting new spans to its span id after that span finishes, so Context links are
// never retargeted.
enum class SpanLinkKind : uint8_t
{
    Span = 0,
    Context = 1,
};

struct Span
{
    uint64_t span_id;
    uint64_t local_root_span_id;
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
    uint64_t identifier;

    bool is_logical() const { return domain != SpanLinkDomain::Thread; }

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

// An empty span on a logical key is a tombstone: the task or greenlet is alive but has no span. birth is taken from a
// global counter when a logical key is created, and does not change while the key exists, whatever is written to it.
// The sampler uses it to ignore keys created after it started unwinding a thread, which can only belong to a new
// object at a reused address. Thread keys are never filtered and use 0.
struct SpanLinkEntry
{
    std::optional<Span> span;
    uint64_t birth = 0;

    bool operator==(const SpanLinkEntry& other) const { return span == other.span && birth == other.birth; }
};

// Runs fn under mtx. The uncontended path never calls on_contention. On contention, on_contention gets a function that
// blocks on mtx, runs fn and releases mtx before returning, so a caller that releases the GIL around it re-acquires
// the GIL only after the mutex is free again. fn never calls into Python.
template<typename Fn, typename OnContention>
void
run_locked(std::mutex& mtx, Fn&& fn, OnContention&& on_contention)
{
    std::unique_lock<std::mutex> lock(mtx, std::try_to_lock);
    if (lock.owns_lock()) {
        fn();
        return;
    }
    on_contention([&] {
        std::lock_guard<std::mutex> guard(mtx);
        fn();
    });
}

// Native span attribution, keyed by execution unit.
//
// There are two sets of operations:
//
// - The thread-only operations (link_span, unlink_span, unlink_finished_span, reset) keep the pre-existing behavior
//   and ignore the lifecycle token. They index every link they write, whatever its kind.
// - The keyed operations (link, retarget, holds_span, clear, get_entry, open, close) follow the per-execution-unit
//   design. Writes are accepted only with the current lifecycle token, and only while that token is odd. That lets a
//   writer that started before a stop, or that runs while stopped, become a no-op without extra bookkeeping. They take
//   a contention hook (see run_locked) so that callers holding the GIL release it only when the mutex is contended.
class SpanLinks
{
  public:
    static SpanLinks& get_instance()
    {
        static SpanLinks instance;
        return instance;
    }

    // Delete Copy constructor and assignment operator to prevent copies
    SpanLinks(SpanLinks const&) = delete;
    SpanLinks& operator=(SpanLinks const&) = delete;

    // Called with a function that blocks on the mutex, does the work and releases the mutex. Native callers, which
    // never hold the GIL, block in place.
    struct BlockInPlace
    {
        template<typename Fn>
        void operator()(Fn&& fn) const
        {
            fn();
        }
    };

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

    // Odd while span links are open (profiling runs), even while they are closed. Writers read it before they read
    // any tracer state and pass it back to link and retarget.
    uint64_t lifecycle() const { return lifecycle_token.load(std::memory_order_acquire); }

    // The birth of the most recently created logical key. A key whose birth is greater than a value read earlier
    // was created after that read.
    uint64_t birth_watermark() const { return birth_counter.load(std::memory_order_acquire); }

    // Writes span (or "no span" when empty) to the current thread's key when thread_id is set, and to the task and
    // greenlet keys whose id is non-zero. "No span" erases a thread key, leaves a tombstone on an existing logical
    // key, and creates nothing for a missing logical key. Returns false, and writes nothing, if token is stale or
    // even.
    template<typename OnContention = BlockInPlace>
    bool link(uint64_t token,
              std::optional<uint64_t> thread_id,
              uint64_t task_id,
              uint64_t greenlet_id,
              const std::optional<Span>& span,
              OnContention&& on_contention = {})
    {
        bool accepted = false;
        with_lock([&] { accepted = link_locked(token, thread_id, task_id, greenlet_id, span); }, on_contention);
        return accepted;
    }

    // Points every Span link that holds span_id at replacement. An empty replacement follows the "no span" rule of
    // link. Idempotent. Returns false, and writes nothing, if token is stale or even.
    template<typename OnContention = BlockInPlace>
    bool retarget(uint64_t token,
                  uint64_t span_id,
                  const std::optional<Span>& replacement,
                  OnContention&& on_contention = {})
    {
        bool accepted = false;
        with_lock(
          [&] {
              if (token_is_current_locked(token)) {
                  retarget_locked(span_id, replacement);
                  accepted = true;
              }
          },
          on_contention);
        return accepted;
    }

    // Whether any Span link still holds span_id.
    template<typename OnContention = BlockInPlace>
    bool holds_span(uint64_t span_id, OnContention&& on_contention = {})
    {
        bool held = false;
        with_lock([&] { held = span_to_keys.find(span_id) != span_to_keys.end(); }, on_contention);
        return held;
    }

    // Physically removes one key, tombstone included. This needs no token: removal is always safe.
    template<typename OnContention = BlockInPlace>
    void clear(SpanLinkKey key, OnContention&& on_contention = {})
    {
        with_lock([&] { remove_locked(key); }, on_contention);
    }

    // Returns the entry for key, or nothing if the key does not exist.
    template<typename OnContention = BlockInPlace>
    std::optional<SpanLinkEntry> get_entry(SpanLinkKey key, OnContention&& on_contention = {})
    {
        std::optional<SpanLinkEntry> entry;
        with_lock(
          [&] {
              auto it = key_to_entry.find(key);
              if (it != key_to_entry.end()) {
                  entry = it->second;
              }
          },
          on_contention);
        return entry;
    }

    // Clear every key and advance the token to the next odd (open) or even (close) value, under one hold of the
    // mutex. A writer blocked on the mutex therefore sees the new token once it gets the lock.
    template<typename OnContention = BlockInPlace>
    void open(OnContention&& on_contention = {})
    {
        with_lock([&] { advance_lifecycle_locked(true); }, on_contention);
    }

    template<typename OnContention = BlockInPlace>
    void close(OnContention&& on_contention = {})
    {
        with_lock([&] { advance_lifecycle_locked(false); }, on_contention);
    }

    // Keeps the lifecycle token and the birth counter. Only the forking thread exists in the child, so no write can
    // be in flight.
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

    template<typename Fn, typename OnContention>
    void with_lock(Fn&& fn, OnContention&& on_contention)
    {
        run_locked(mtx, fn, on_contention);
    }

    bool token_is_current_locked(uint64_t token) const
    {
        return (token & 1U) != 0 && token == lifecycle_token.load(std::memory_order_relaxed);
    }

    bool link_locked(uint64_t token,
                     std::optional<uint64_t> thread_id,
                     uint64_t task_id,
                     uint64_t greenlet_id,
                     const std::optional<Span>& span);
    void assign_locked(const SpanLinkKey& key, std::optional<Span> span);
    void retarget_locked(uint64_t span_id, const std::optional<Span>& replacement);
    void remove_locked(const SpanLinkKey& key);
    void index_locked(const SpanLinkKey& key, uint64_t span_id);
    void unindex_locked(const SpanLinkKey& key, uint64_t span_id);
    void unindex_locked(const SpanLinkKey& key, const std::optional<Span>& span);
    void clear_maps_locked();
    void advance_lifecycle_locked(bool open);

    std::mutex mtx;
    KeyToEntry key_to_entry;
    // Covers exactly the entries whose span has kind Span. link_span always writes that kind.
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
