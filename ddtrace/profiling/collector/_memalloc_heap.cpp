#include <cassert>
#include <cstdint>
#include <memory>
#include <random>
#include <vector>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "_memalloc_debug.h"
#include "_memalloc_gc_guard.hpp"
#include "_memalloc_heap.h"
#include "_memalloc_reentrant.h"
#include "_memalloc_tb.h"
#include "_pymacro.h"

/* Use Abseil's flat_hash_map for tracking sampled allocations.
 * flat_hash_map provides excellent performance with low memory overhead,
 * using the Swiss Tables algorithm from Abseil.
 *
 * We use a conditional compilation to fall back to std::unordered_map
 * when Abseil is not available (e.g., in Debug builds).
 */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/container/flat_hash_map.h"
template<typename K, typename V>
using HeapMapType = absl::flat_hash_map<K, V>;
#else
#include <unordered_map>
template<typename K, typename V>
using HeapMapType = std::unordered_map<K, V>;
#endif // defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)

/*
   How heap profiler sampling works:

   This is mostly derived from
 https://github.com/google/tcmalloc/blob/master/docs/sampling.md#detailed-treatment-of-weighting-weighting

   We want to explain memory used by the program. We can't track every
   allocation with reasonable overhead, so we sample. We'd like the heap to
   represent what's taking up the most memory. We'd like to see large live
   allocations, or when many small allocations in some part of the code add up
   to a lot of memory usage. So, we choose to sample based on bytes allocated.
   We basically want every byte allocated to have the same probability of being
   represented in the profile. Assume we want an average of one byte out of
   every R allocated sampled. Call R the "sampling interval". In a simplified
   world where every allocation is 1 byte, we can just do a 1/R coin toss for
   every allocation.  This can be simplified by observing that the interval
   between samples done this way follows a geometric distribution with average
   R. We can draw from a geometric distribution to pick the next sample point.
   For computational simplicity, we use an exponential distribution, which is
   essentially the limit of the geometric distribution if we were to divide each
   byte into smaller and smaller sub-bytes. We set a target for sampling, T,
   drawn from the exponential distribution with average R. We count the number
   of bytes allocated, C. For each allocation, we increment C by the size of the
   allocation, and when C >= T, we take a sample, reset C to 0, and re-draw T.

   If we reported just the sampled allocation's sizes, we would significantly
   misrepresent the actual heap size. We're probably going to hit some small
   allocations with our sampling, and reporting their actual size would
   under-represent the size of the heap. Each sampled allocation represents
   roughly R bytes of actual allocated memory. We want to weight our samples
   accordingly, and account for the fact that large allocations are more likely
   to be sampled than small allocations.

   The math for weighting is described in more detail in the tcmalloc docs.
   Basically, any sampled allocation should get an average weight of R, our
   sampling interval. However, this would under-weight allocations larger than R
   bytes, our sampling interval. When we pick the next sampling point, it's
   probably going to be in the middle of an allocation. Bytes of the sampled
   allocation past that point are going to be skipped by our sampling method,
   since we re-draw the target _after_ the allocation. We can correct for this
   by looking at how big the allocation was, and how much it would drive the
   counter C past the target T. The formula W = R + (C - T) expresses this,
   where C is the counter including the sampled allocation. If the allocation
   was large, we are likely to have significantly exceeded T, so the weight will
   be larger. Conversely, if the allocation was small, C - T will likely be
   small, so the allocation gets less weight, and as we get closer to our
   hypothetical 1-byte allocations we'll get closer to a weight of R for each
   allocation. The current code simplifies this a bit. We can also express the
   weight as C + (R - T), and note that on average T should equal R, and just
   drop the (R - T) term and use C as the weight. We might want to use the full
   formula if more testing shows us to be too inaccurate.
 */

class heap_tracker_t
{
  public:
    /* Constructor - does not make any C Python API calls */
    heap_tracker_t(uint32_t sample_size_val);
    ~heap_tracker_t() = default;

    // Delete copy constructor and assignment operator
    heap_tracker_t(const heap_tracker_t&) = delete;
    heap_tracker_t& operator=(const heap_tracker_t&) = delete;

    /* Remove an allocation at the given address, if we are tracking it. This
     * function accesses the heap tracker data structures. It must be called with the
     * GIL held and must not make any C Python API calls. The traceback is deleted
     * internally if found. */
    void untrack_no_cpython(void* ptr);

    /* Decide whether we should sample an allocation of the given size. Accesses
     * shared state, and must be called with the GIL held and without making any C
     * Python API calls. Returns true if we should sample, and sets allocated_memory_val
     * to the current allocated_memory value. */
    bool should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val);

    /* Track an allocation that we decided to sample. This updates shared state and
     * must be called with the GIL held and without making any C Python API calls.
     * If an allocation at the same address is already tracked, the old traceback
     * is deleted internally. */
    void add_sample_no_cpython(void* ptr, std::unique_ptr<traceback_t> tb);

    void export_heap_no_cpython();

    /* Global instance of the heap tracker */
    static heap_tracker_t* instance;

    /* Traceback pool operations */
    std::unique_ptr<traceback_t> pool_get_with_alloc_data_invokes_cpython(size_t size,
                                                                          size_t weighted_size,
                                                                          uint16_t max_nframe);
    void pool_put_no_cpython(std::unique_ptr<traceback_t> tb);

    /* Reset the heap tracker state after fork in child process */
    void postfork_child();

  private:
    /* Delta-export change event. ADD events reference the live traceback in
     * allocs_m via raw pointer; REMOVE events own the extracted unique_ptr.
     *
     * The raw pointer in ADD is valid until export because the traceback is
     * owned by either allocs_m or a subsequent REMOVE event in pending_changes
     * (both are cleared together at export time). REMOVE events emit a
     * negative tombstone — heap_space is negated lazily at emit time so an
     * earlier ADD that aliases the same traceback (same ptr, same snapshot
     * interval) reads the original positive value. `tombstone_applied` guards
     * against re-negation when an emit fails and we retry. */
    struct change_event
    {
        enum Kind : uint8_t
        {
            ADD,
            REMOVE
        };
        void* ptr;
        traceback_t* tb;
        std::unique_ptr<traceback_t> owner; // set for REMOVE, null for ADD
        Kind kind;
        bool tombstone_applied = false; // REMOVE only; true after heap_space is negated
        uint8_t failed_attempts = 0;    // export retries counter; events exceeding MAX are dropped
    };

    /* Cap retries on a single event before dropping it, so a persistent
     * libdatadog rejection cannot grow pending_changes without bound. */
    static constexpr uint8_t MAX_EXPORT_RETRIES = 2;

    uint32_t next_sample_size_no_cpython(uint32_t sample_size);

    /* This function is called from heap_tracker_t::postfork_child() as part of
       the fork handler to reset the sampling state. */
    void reset_sampling_state_no_cpython();

    /* Heap profiler sampling interval */
    uint64_t sample_size;

    /* Per-instance PRNG engine used by next_sample_size_no_cpython.
     * Declared before current_sample_size so it is initialised first in the
     * constructor member-initialiser list, allowing next_sample_size_no_cpython
     * to be called safely during current_sample_size initialisation.
     * std::minstd_rand stores all state in the object (no global locks), so it
     * is fork-safe (unlike rand). */
    std::minstd_rand rng;
    /* Next heap sample target, in bytes allocated */
    uint64_t current_sample_size;
    /* Tracked allocations - using unique_ptr for automatic memory management */
    HeapMapType<void*, std::unique_ptr<traceback_t>> allocs_m;
    /* Bytes allocated since the last sample was collected */
    uint64_t allocated_memory;

    /* Debug guard to assert that GIL-protected critical sections are maintained
     * while accessing the profiler's state */
    memalloc_gil_debug_check_t gil_guard;

    /* Traceback pool - reduces allocation overhead. Access is always under GIL. */
    static constexpr size_t POOL_CAPACITY = 128;
    std::vector<std::unique_ptr<traceback_t>> pool;

    /* Initial capacity of the allocations map */
    static constexpr size_t INITAL_ALLOC_MAP_CAPACITY = 512;

    /* Delta export state. pending_changes accumulates ADD/REMOVE events between
     * snapshots; export_heap_no_cpython drains it and emits the deltas.
     *
     * Pre-reserved at construction so steady-state push_back is allocation-free.
     * On pathological bursts the vector may reallocate. The reallocation goes
     * through ::operator new (system malloc), NOT PyMem_Malloc, so it does not
     * reenter the Python allocator hooks where untrack_no_cpython runs from.
     * (Reentry would also be caught by memalloc_reentrant_guard_t earlier in
     * the call.) Dropping events at the push site instead would risk
     * use-after-clear on a queued ADD whose traceback gets pool_put'd by a
     * matching REMOVE that overflowed — letting the vector grow is the safe
     * option. */
    static constexpr size_t PENDING_CHANGES_RESERVE = 4096;
    std::vector<change_event> pending_changes;

    /* Pair-collapse side map: ptr -> index of its pending ADD event in
     * pending_changes. When untrack arrives for a ptr that has a still-pending
     * ADD (i.e., the allocation was tracked and freed within the same snapshot
     * interval), both events are canceled before reaching libdatadog — the ADD
     * is marked `collapsed`, no REMOVE is queued, and the traceback returns to
     * the pool immediately. Saves ~2 Profile_add2 calls per churn pair, which
     * dominates CPU on alloc-then-free hot loops (see PR #18125 review). */
    static constexpr size_t PENDING_ADD_IDX_RESERVE = 1024;
    HeapMapType<void*, size_t> pending_add_idx;
};

// Pool implementation
// _invokes_cpython suffix: calls traceback_t::reset() and constructor which invoke CPython APIs
std::unique_ptr<traceback_t>
heap_tracker_t::pool_get_with_alloc_data_invokes_cpython(size_t size, size_t weighted_size, uint16_t max_nframe)
{
    /* Try to get a traceback from the pool */
    if (!pool.empty()) {
        auto tb = std::move(pool.back());
        pool.pop_back();
        /* Initialize it with the new allocation data */
        tb->init_sample(size, weighted_size, max_nframe);
        return tb;
    }

    /* Pool is empty, create a new traceback */
    return std::make_unique<traceback_t>(size, weighted_size, max_nframe);
}

void
heap_tracker_t::pool_put_no_cpython(std::unique_ptr<traceback_t> tb)
{
    if (!tb) {
        return;
    }

    /* Clear buffers before returning to pool to prevent memory leaks */
    tb->sample.clear();

    /* Try to return the traceback to the pool */
    if (pool.size() < POOL_CAPACITY) {
        pool.push_back(std::move(tb));
    }
    /* If pool is full, tb automatically deletes the traceback when it goes out of scope */
}

uint32_t
heap_tracker_t::next_sample_size_no_cpython(uint32_t sample_size)
{
    /* Draw a sampling target from an exponential distribution with mean
       sample_size. std::exponential_distribution handles the inverse-transform
       sampling internally.

       NOTE: std::exponential_distribution calls log internally. log is not
       listed as async-signal-safe by POSIX, but does not use locks in practice.
       We assume it is safe to call from heap_tracker_t::postfork_child. */
    std::exponential_distribution<double> dist(1.0 / (sample_size + 1));
    return static_cast<uint32_t>(dist(rng));
}

// Method implementations
heap_tracker_t::heap_tracker_t(uint32_t sample_size_val)
  : sample_size(sample_size_val)
  , rng(sample_size_val != 0U ? sample_size_val : 0x9e3779b9U) // 2^32 / phi (golden ratio)
  , current_sample_size(next_sample_size_no_cpython(sample_size_val))
  , allocated_memory(0)
{
    // Pre-allocate pool capacity to avoid reallocations
    pool.reserve(POOL_CAPACITY);
    // Pre-allocate map capacity to avoid rehashing during ramp-up.
    allocs_m.reserve(INITAL_ALLOC_MAP_CAPACITY);
    pending_changes.reserve(PENDING_CHANGES_RESERVE);
    pending_add_idx.reserve(PENDING_ADD_IDX_RESERVE);
}

void
heap_tracker_t::untrack_no_cpython(void* ptr)
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    auto node = allocs_m.extract(ptr);
    if (node.empty()) {
        return;
    }
    auto owner = std::move(node.mapped());

    /* Pair-collapse: if a still-pending ADD targets this same ptr, the
     * allocation was tracked AND freed within this snapshot interval — both
     * events would aggregate to a zero bucket on the backend anyway, so skip
     * both and reclaim the traceback immediately. Eliminates the dominant
     * libdatadog cost in alloc-then-free hot loops.
     *
     * Swap-and-pop keeps pending_changes compact instead of leaving the ADD
     * marked dead in place: the last element moves into the freed slot, and
     * if it's also a tracked ADD, its index entry in pending_add_idx is
     * rewritten to point at the new position. Avoids paying the export-time
     * iteration cost over a buffer that fills up with skipped entries under
     * sustained alloc/free churn. */
    auto it = pending_add_idx.find(ptr);
    if (it != pending_add_idx.end()) {
        const size_t add_idx = it->second;
        const size_t last_idx = pending_changes.size() - 1;
        if (add_idx != last_idx) {
            pending_changes[add_idx] = std::move(pending_changes[last_idx]);
            // The moved element kept its own pending_add_idx entry pointing at
            // last_idx; rewrite it if the moved element was an indexed ADD.
            if (pending_changes[add_idx].kind == change_event::ADD) {
                pending_add_idx[pending_changes[add_idx].ptr] = add_idx;
            }
        }
        pending_changes.pop_back();
        pending_add_idx.erase(it);
        pool_put_no_cpython(std::move(owner));
        return;
    }

    /* No pending ADD — the allocation was tracked in an earlier snapshot
     * interval and the backend already integrated its positive contribution.
     * Queue a REMOVE so the next export emits a negative tombstone. heap_space
     * negation is deferred to emit time so any same-snapshot raw-ptr aliasing
     * sees the original positive value first. */
    auto* raw = owner.get();
    pending_changes.push_back({ ptr, raw, std::move(owner), change_event::REMOVE });
}

bool
heap_tracker_t::should_sample_no_cpython(size_t size, uint64_t* allocated_memory_val)
{
    memalloc_gil_debug_guard_t guard(gil_guard);
    allocated_memory += size;
    *allocated_memory_val = allocated_memory;

    /* Check if we have enough sample or not */
    if (allocated_memory < current_sample_size) {
        return false;
    }

    if (allocs_m.size() > TRACEBACK_ARRAY_MAX_COUNT) {
        /* TODO(nick) this is vestigial from the original array-based
         * implementation. Do we actually want this? It gives us bounded memory
         * use, but the size limit is arbitrary and once we hit the arbitrary
         * limit our reported numbers will be inaccurate.
         */
        return false;
    }

    return true;
}

void
heap_tracker_t::add_sample_no_cpython(void* ptr, std::unique_ptr<traceback_t> tb)
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    auto [it, inserted] = allocs_m.insert_or_assign(ptr, std::move(tb));

    /* This should always be a new insertion. If not, we failed to properly untrack a previous allocation. */
    assert(inserted && "add_sample: found existing entry for key that should have been removed");

    /* Record ADD event referencing the live traceback. The raw pointer remains
     * valid until export: either the entry stays in allocs_m, or a subsequent
     * untrack moves ownership into a REMOVE event in pending_changes — both
     * are cleared in the same export pass. Index this event in pending_add_idx
     * so a same-snapshot-interval free can find and collapse it. */
    pending_changes.push_back({ ptr, it->second.get(), nullptr, change_event::ADD });
    pending_add_idx[ptr] = pending_changes.size() - 1;

    // Get ready for the next sample
    reset_sampling_state_no_cpython();
}

void
heap_tracker_t::export_heap_no_cpython()
{
    memalloc_gil_debug_guard_t guard(gil_guard);

    /* Delta emission: ADDs reference live tracebacks via raw pointer (kept
     * alive by either allocs_m or a later REMOVE event in this buffer).
     * REMOVEs own the extracted unique_ptr and emit a negative tombstone;
     * heap_space is negated lazily here (guarded by tombstone_applied) so an
     * earlier ADD aliasing the same traceback emits the original positive
     * value before the REMOVE flips the sign, and so retries on libdatadog
     * rejection don't toggle the sign each attempt.
     *
     * On libdatadog rejection, retain the event for retry on the next snapshot
     * — without this, an ADD or REMOVE that Profile_add2 refuses is silently
     * dropped, causing permanent drift in the backend's running heap state.
     * Bounded by MAX_EXPORT_RETRIES so a persistent rejection cannot grow
     * pending_changes without bound.
     *
     * No periodic resync is emitted today: a full-snapshot would not carry a
     * wire-format marker telling the backend to reset accumulated state, so
     * the backend would double-count instead of recovering from drift. If a
     * delta upload is dropped, the backend's state diverges from live until
     * the next profiler restart (which clears allocs_m and pending_changes).
     * Adding a resync requires either backend-side reset signaling or
     * emitting compensating negative+positive pairs for every live entry —
     * both are out of scope for v1. */

    /* In-place compaction with a write index. Moving into a fresh vector and
     * swapping would discard the constructor-side reserve (PENDING_CHANGES_RESERVE)
     * on every export, forcing the hook path to re-grow the buffer on the next
     * churn burst. Keeping the same storage preserves capacity. */
    size_t write_idx = 0;
    for (size_t read_idx = 0; read_idx < pending_changes.size(); ++read_idx) {
        auto& evt = pending_changes[read_idx];
        if (evt.kind == change_event::REMOVE && !evt.tombstone_applied) {
            evt.tb->sample.negate_heap_space();
            evt.tombstone_applied = true;
        }
        if (evt.tb->sample.export_sample()) {
            // Success: recycle the REMOVE traceback; ADDs leave their
            // tracebacks in allocs_m where they were.
            if (evt.kind == change_event::REMOVE) {
                pool_put_no_cpython(std::move(evt.owner));
            }
            continue;
        }
        // libdatadog rejected: retry next snapshot, up to MAX_EXPORT_RETRIES.
        if (evt.failed_attempts >= MAX_EXPORT_RETRIES) {
            if (evt.kind == change_event::REMOVE) {
                pool_put_no_cpython(std::move(evt.owner));
            }
            continue;
        }
        ++evt.failed_attempts;
        if (write_idx != read_idx) {
            pending_changes[write_idx] = std::move(evt);
        }
        ++write_idx;
    }
    pending_changes.resize(write_idx);
    /* Rebuild the side map for the compacted pending_changes vector. The old
     * map's indices referenced positions before compaction; without rebuilding,
     * retained ADDs would lose their pair-collapse capability on the next
     * snapshot (a REMOVE arriving for one of these ptrs would queue a tombstone
     * instead of canceling the still-unsuccessful ADD). The cost is O(write_idx),
     * which is bounded — retained events only appear on libdatadog rejection
     * and are capped at MAX_EXPORT_RETRIES per event. */
    pending_add_idx.clear();
    for (size_t i = 0; i < pending_changes.size(); ++i) {
        if (pending_changes[i].kind == change_event::ADD) {
            pending_add_idx[pending_changes[i].ptr] = i;
        }
    }

    Datadog::Sample::profile_borrow().stats().set_heap_tracker_size(allocs_m.size());
}

void
heap_tracker_t::reset_sampling_state_no_cpython()
{
    allocated_memory = 0;
    current_sample_size = next_sample_size_no_cpython(sample_size);
}

void
heap_tracker_t::postfork_child()
{
    // As we're in the child process after fork, we want to make sure that the
    // heap tracker state is consistent before running any Python code. If not,
    // we may end up triggering memory profiler code with an inconsistent state,
    // leading to undefined behaviors and/or crashes, ref: incident-48649.
    // To avoid this, we clear the heap tracker state here.

    // Sample pool contains traceback_t objects, which reference the global
    // Profile state. Global Profile state is reset after fork in
    // Profile::postfork_child()
    pool.clear();

    // Pending delta events may contain raw pointers into allocs_m and owned
    // tracebacks; clear before dropping allocs_m so the raw pointers in any
    // ADD events do not get stranded. Side map indices alias these events.
    pending_changes.clear();
    pending_add_idx.clear();

    // Allocations map may contain data from the parent process, and also
    // traceback_t objects may reference invalid Profile state.
    allocs_m.clear();

    // Reset the sampling state to start fresh after fork.
    reset_sampling_state_no_cpython();
}

// Static member definition
heap_tracker_t* heap_tracker_t::instance = nullptr;

/* Public API */

bool
memalloc_heap_tracker_init_no_cpython(uint32_t sample_size)
{
    // TODO(dsn): what should we do it this was already initialized?
    if (!heap_tracker_t::instance) {
        heap_tracker_t::instance = new heap_tracker_t(sample_size);
        return true;
    }
    return false;
}

void
memalloc_heap_tracker_deinit_no_cpython(void)
{
    // Delete the instance and set to nullptr. We set to nullptr first so that
    // if the destructor releases the GIL, we can use nullptr as a sentinel.
    heap_tracker_t* old_instance = heap_tracker_t::instance;
    heap_tracker_t::instance = nullptr;
    delete old_instance;
}

void
memalloc_heap_untrack_no_cpython(void* ptr)
{
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->untrack_no_cpython(ptr);
    }
}

/* Track a memory allocation in the heap profiler. */
void
memalloc_heap_track_invokes_cpython(uint16_t max_nframe, void* ptr, size_t size, PyMemAllocatorDomain domain)
{
    (void)domain; // Parameter kept for API consistency but not currently used
    if (!heap_tracker_t::instance) {
        return;
    }
    uint64_t allocated_memory_val = 0;
    if (!heap_tracker_t::instance->should_sample_no_cpython(size, &allocated_memory_val)) {
        return;
    }

    /* Skip tracking if we're already inside the malloc hook on this thread.
     * Reentrant tracking would corrupt the heap tracker's data structures. */
    memalloc_reentrant_guard_t guard;
    if (!guard) {
        return;
    }

    /* PR #14550 — realloc+GC use-after-free crash.
       Prior to Python 3.12, and particularly in Python 3.11, collecting
       tracebacks while intercepting allocations is prone to crashes. We
       currently use the C Python API to collect tracebacks, which can
       do allocations. These allocations can in turn trigger garbage collection,
       allowing other code to run. In the past we've seen this lead to
       GIL release and cause corruption in the memory profiler.

       This can also lead to use-after-free crashes. For example, calling
       realloc to grow a data structure, we can trigger garbage collection which
       visits the data structure. The underlying reallocaiton will have already
       happened (we want to track the new address) but the data structure will
       still point to old memory since our wrapper hasn't returned.

       Python 3.12 doesn't trigger GC during allocation. Instead, a flag is set
       for GC to run later, at a safe point in the interpreter. But for earlier
       versions, we disable GC temporarily. This will allow a small, temporary
       increase in memory usage during sampling. But it is overall cheap (mostly
       just toggling a boolean) and the alternative is hard-to-diagnose crashes.

       RAII guard automatically re-enables GC when it goes out of scope. */
#if defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)
    pygc_temp_disable_guard_t gc_guard;
#endif // defined(_PY310_AND_LATER) && !defined(_PY312_AND_LATER)

    /* The weight of the allocation is described above, but briefly: it's the
       count of bytes allocated since the last sample, including this one, which
       will tend to be larger for large allocations and smaller for small
       allocations, and close to the average sampling interval so that the sum
       of sample live allocations stays close to the actual heap size */

    // Check that instance is valid before creating traceback
    if (!heap_tracker_t::instance) {
        return;
    }

    auto tb =
      heap_tracker_t::instance->pool_get_with_alloc_data_invokes_cpython(size, allocated_memory_val, max_nframe);

    // Export allocation sample right away to avoid holding it
    tb->sample.export_sample();
    // Reset the allocation data, keep heap data for tracking
    tb->sample.reset_alloc();
    // pool_get_with_alloc_data_invokes_cpython() creates sample with allocation data only (no heap data)
    // to avoid double-pushing allocation data, we manually push heap data here.
    // Use the weighted size (allocated_memory_val) so the heap profile accounts
    // for sampling, matching the tcmalloc/Go pprof approach: each sampled live
    // allocation represents ~R bytes of heap, not just its own raw size.
    tb->sample.push_heap(allocated_memory_val);

    // Check that instance is still valid after GIL release in constructor
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->add_sample_no_cpython(ptr, std::move(tb));
    }
    // If instance is gone, tb's unique_ptr automatically deletes the traceback
}

void
memalloc_heap_no_cpython(void)
{
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->export_heap_no_cpython();
    }
}

void
memalloc_heap_postfork_child(void)
{
    if (heap_tracker_t::instance) {
        heap_tracker_t::instance->postfork_child();
    }
}
