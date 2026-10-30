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

// The kind of execution unit a key names. An OS thread can run many asyncio tasks or greenlets, switching between
// them, so each of those gets its own key too. Tasks and greenlets are called logical units here, because they are not
// OS threads. Keeping the domain in the key stops a task's address from colliding with an equal thread id.
enum class SpanLinkDomain : uint8_t
{
    Thread = 0,
    AsyncioTask = 1,
    Greenlet = 2,
};

// What the tracer had active when the link was written. tracer.context_provider.active() returns either a ddtrace Span
// or a ddtrace Context (ActiveTrace in ddtrace/_trace/provider.py). Both have a trace id and a span id, and a new span
// uses the active one's span id as its parent id. They differ in what happens when that span finishes.
enum class SpanLinkKind : uint8_t
{
    // A live span this process started, for example with tracer.trace("db.query"). When it finishes, the tracer makes
    // its parent active again, so retarget moves the links that hold it to the parent.
    Span = 0,
    // A trace id and a span id with no live span object behind them. The main case is a thread pool:
    // ThreadPoolExecutor.submit copies the caller's context and activates the copy in the worker thread for the whole
    // job. If the caller's span finishes on its own thread first, the worker's new spans still use its span id as their
    // parent, so the worker's samples should keep pointing at it too. Context links are therefore never retargeted.
    // Incoming distributed tracing headers also produce a Context, whose span id belongs to the upstream service.
    Context = 1,
};

// The active item a link points at. Despite the name, it holds either kind of active item, and kind says which.
struct Span
{
    // The active Span's id, or the active Context's span id.
    uint64_t span_id;
    // The local root span is the first span of this trace in this process, usually the request span. Samples carry
    // its id and type so they can be grouped by request.
    uint64_t local_root_span_id;
    // The local root span's type, for example "web". Samples report it as their trace type.
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
    // For a thread, its PyThreadState thread id (the same id the sampler uses). For a task or greenlet, the object's
    // address, which is what id(task) or id(greenlet) returns in Python.
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

// What one execution unit is running.
struct SpanLinkEntry
{
    // Empty when the unit has no active span. A thread key with no span is erased. A task or greenlet key with no span
    // is kept, empty, as a tombstone, so that it keeps its birth while the object is alive.
    std::optional<Span> span;

    // Guards against address reuse. Python can free a task and create a new one at the same address. Example: the
    // sampler starts unwinding a thread, task A at address 0x10 ends and is freed, and task B is created at 0x10. The
    // sampler must not attach B's span to A's frames. So each new task or greenlet key takes the next value of a global
    // counter as its birth. The sampler reads birth_watermark() before it unwinds a thread and ignores keys with a
    // larger birth. A key keeps its birth until it is removed, whatever is written to it. That is why "no span"
    // leaves a tombstone: if A's key were erased and then re-created during the unwind, A itself would be ignored.
    // Thread keys are never ignored and use 0.
    uint64_t birth = 0;

    bool operator==(const SpanLinkEntry& other) const { return span == other.span && birth == other.birth; }
};

// The span labels of one task or greenlet sample, as the sampler read them for it.
struct LogicalSpan
{
    // False while the domain's set_logical_attribution switch is off. The sample then gets its thread's span, as it
    // did before tasks and greenlets had keys of their own.
    bool attributed = false;
    // When attributed is true, the unit's own span. Empty means the sample gets no span at all. It never falls back to
    // the thread's span, because that span may belong to another task or greenlet running on the same thread.
    std::optional<Span> span;
};

// Runs fn under mtx. If mtx is free, fn runs right away and on_contention is never called. If mtx is held,
// on_contention gets a function that blocks on mtx, runs fn and releases mtx before returning.
//
// This exists for Python callers, which hold the GIL. If they blocked on mtx while holding it, every other Python
// thread would stall until mtx was free. Instead their hook releases the GIL, calls the function, and takes the GIL
// back. This cannot deadlock, because whoever holds mtx (the sampling thread, or another writer that already released
// the GIL) never needs the GIL while holding it. fn must not call into Python.
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

// Records which span each execution unit is running, so the stack sampler can tag each sample with the span that was
// active when the sample was taken. Tracer hooks in Python write it when a span is activated or finishes. The
// sampling thread reads it without the GIL. mtx protects both maps.
//
// - key_to_entry maps each execution unit to what it is running.
// - span_to_keys maps a span id to the keys whose entry is a Span-kind link to it. When a span finishes, it finds the
//   links to move without scanning every key.
//
// Python writers use the keyed operations (link, retarget, holds_span, clear, get_entry, open, close). Writes need the
// current lifecycle token (see lifecycle()), and every operation takes a contention hook (see run_locked). A few
// native callers use the thread-only helpers: the sampler reads a thread's span with get_active_span_from_thread_id,
// thread exit erases the thread's key with unlink_span, and stopping the sampler clears both maps with reset. These
// ignore the lifecycle token.
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

    // The default contention hook: just block on the mutex. For native callers, which never hold the GIL.
    struct BlockInPlace
    {
        template<typename Fn>
        void operator()(Fn&& fn) const
        {
            fn();
        }
    };

    const std::optional<Span> get_active_span_from_thread_id(uint64_t thread_id);
    void unlink_span(uint64_t thread_id);
    void reset();

    // The lifecycle token. It is odd while span links are open (the profiler runs) and even while they are closed, and
    // every open() or close() advances it. A writer reads it before it reads any tracer state, and passes it to link or
    // retarget. The write is dropped unless the token is still current and odd. Example: a writer reads token 3, then
    // the profiler stops (token 4) and starts again (token 5) before the writer gets the mutex. The span the writer
    // read belongs to the old run, so its write with token 3 is dropped instead of leaking into the new run.
    uint64_t lifecycle() const { return lifecycle_token.load(std::memory_order_acquire); }

    // The birth of the most recently created task or greenlet key (see SpanLinkEntry::birth). A key whose birth is
    // greater than a value read earlier was created after that read.
    uint64_t birth_watermark() const { return birth_counter.load(std::memory_order_acquire); }

    // Writes span to the calling thread's key if thread_id is set, and to the task and greenlet keys whose id is not
    // 0, all under one hold of the mutex. An empty span means "no span": it erases a thread key, makes an existing task
    // or greenlet key a tombstone, and creates nothing for a missing one, so untraced tasks cost no entry. Returns
    // false, and writes nothing, unless token is current and odd.
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

    // Called when the span span_id finishes, with what the tracer makes active next, usually its parent. Points every
    // Span-kind link that holds span_id at replacement, and leaves Context-kind links alone (see SpanLinkKind). An
    // empty replacement follows the "no span" rule of link. Calling it twice has the same effect as calling it once.
    // Returns false, and writes nothing, unless token is current and odd.
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

    // Whether any Span-kind link still holds span_id. Context-kind links are not counted.
    template<typename OnContention = BlockInPlace>
    bool holds_span(uint64_t span_id, OnContention&& on_contention = {})
    {
        bool held = false;
        with_lock([&] { held = span_to_keys.find(span_id) != span_to_keys.end(); }, on_contention);
        return held;
    }

    // Removes one key, tombstone included, for example when its task is freed. This needs no token, because removing a
    // key can never leave stale data behind.
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

    // open() starts a lifecycle and close() ends it. Both clear every key and advance the token, to the next odd value
    // for open and the next even value for close, under one hold of the mutex. A writer waiting on the mutex therefore
    // sees the new token once it gets the lock.
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

    // Logical attribution labels a task or greenlet sample with the span of that task or greenlet (its own key),
    // instead of the span of the thread it ran on. There is one switch for AsyncioTask and one for Greenlet, both off
    // by default. While a domain's switch is off, the sampler labels its task or greenlet samples with the thread's
    // span, as before. Once it is on, those samples get only their own key's span, or no span. The switches exist so
    // that the sampler can learn to read task and greenlet keys before anything writes them: with a switch on and no
    // writer, those samples would lose the thread span they get today. They live outside the maps, so open, close and
    // postfork_child leave them alone. The Thread domain has no switch, and setting it does nothing.
    void set_logical_attribution(SpanLinkDomain domain, bool enabled);
    bool logical_attribution(SpanLinkDomain domain) const;

  private:
    using KeySet = std::unordered_set<SpanLinkKey, SpanLinkKeyHash>;
    using KeyToEntry = std::unordered_map<SpanLinkKey, SpanLinkEntry, SpanLinkKeyHash>;
    using SpanToKeys = std::unordered_map<uint64_t, KeySet>;

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
    // Holds exactly the keys whose entry has kind Span. Context-kind entries are left out because they are never
    // retargeted.
    SpanToKeys span_to_keys;

    // Written under mtx. Read without it by lifecycle() and birth_watermark().
    std::atomic<uint64_t> lifecycle_token{ 0 };
    std::atomic<uint64_t> birth_counter{ 0 };

    std::atomic<bool> asyncio_task_attribution{ false };
    std::atomic<bool> greenlet_attribution{ false };

    // Private Constructor/Destructor
    SpanLinks() = default;
    ~SpanLinks() = default;
};

// Reads the span of each task or greenlet the sampler renders on one thread. Create one right before unwinding the
// thread, then call read() for each unit after its frames have been copied.
//
// Creating it reads the domain's switch and birth_watermark(), once for the whole thread. read() ignores a key whose
// birth is larger: that key was created during the unwind, maybe for a new object at the address of a unit whose
// frames were already copied (see SpanLinkEntry::birth).
class LogicalSpanReader
{
  public:
    LogicalSpanReader(SpanLinks& span_links, SpanLinkDomain unit_domain);

    LogicalSpan read(uint64_t unit_id) const;

  private:
    // Null when the domain's switch was off. read() then returns a LogicalSpan that is not attributed.
    SpanLinks* links = nullptr;
    SpanLinkDomain domain = SpanLinkDomain::Thread;
    uint64_t birth_watermark = 0;
};

}
