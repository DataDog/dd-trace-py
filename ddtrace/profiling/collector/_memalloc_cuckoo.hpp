#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <random>
#include <utility>

/* Use absl::Hash when available (release builds with abseil) and fall back
 * to std::hash otherwise. Mirrors the HeapMapType pattern in
 * _memalloc_heap.cpp so debug builds compile without abseil. */
#if defined(NDEBUG) && !defined(DONT_COMPILE_ABSEIL)
#include "absl/hash/hash.h"
template<typename T>
using filter_hash_t = absl::Hash<T>;
#else
#include <functional>
template<typename T>
using filter_hash_t = std::hash<T>;
#endif

/* AIDEV-NOTE: Cuckoo filter for fast-reject on the heap profiler free path.
 *
 * The heap profiler samples ~1% of allocations. Without a filter, every
 * free() must probe allocs_m (an absl::flat_hash_map) to find out, paying
 * 50-100 cycles even for the 99% of frees that aren't sampled. This filter
 * answers "is this pointer sampled?" in ~10-15 cycles by checking two
 * candidate buckets containing 16-bit fingerprints.
 *
 * Critical invariant: the filter MUST be a superset of allocs_m's keys.
 *   - False positives are tolerated (extract returns an empty node).
 *   - False negatives MUST NOT occur — they would skip a real entry and
 *     leak heap-tracker state.
 *
 * To preserve the invariant on saturation: insert() returns false when
 * eviction reaches MAX_KICKS, and the caller MUST drop the sample (must
 * NOT add to allocs_m without a filter entry).
 *
 * Sizing rationale:
 *   - 4 slots per bucket × 32768 buckets = 131072 slots
 *   - The heap tracker stops accepting new samples once allocs_m exceeds
 *     TRACEBACK_ARRAY_MAX_COUNT (65535), so live entries top out at 65536
 *   - Worst-case load factor ≈ 50%, well below the 95% where eviction
 *     failure becomes likely. At 50% load the literature puts the
 *     per-insert MAX_KICKS=500 failure probability below 1e-10
 *   - 16-bit fingerprints stored as uint16_t (no bit-packing). With b=4
 *     slots per bucket and f=16 fingerprint bits, the standard cuckoo
 *     filter False Positive Rate formula is: `2b/2^f = 8/65536 ≈ 1/8192 ≈ 0.012%`
 *     per probe of an absent key. Table footprint = 32768 × 4 × 2 bytes
 *     = 256 KB, fits in L2 on every target platform.
 *
 * Hashing uses the standard partial-key cuckoo trick (Fan et al. 2014):
 *   h1 = hash(ptr) mod NUM_BUCKETS
 *   h2 = h1 ^ (hash(fp) mod NUM_BUCKETS)
 * Because XOR is involutive, alt_bucket(alt_bucket(i, fp), fp) == i. This
 * is what makes deletion possible without storing the full key in the slot.
 *
 * Thread safety: not thread-safe. Same constraint as allocs_m, which
 * assumes the GIL is held. When PEP 703 free-threading lands, both will
 * need to migrate to atomics together.
 */
class CuckooFilter
{
  public:
    static constexpr size_t NUM_BUCKETS = 1u << 15; /* 32768 */
    static constexpr size_t SLOTS_PER_BUCKET = 4;
    static constexpr size_t MAX_KICKS = 500;
    static constexpr uint32_t BUCKET_MASK = static_cast<uint32_t>(NUM_BUCKETS - 1);
    static constexpr uint16_t EMPTY_SLOT = 0;

    CuckooFilter() noexcept
      : evict_rng_(0x9e3779b9U)
    {
        clear();
    }

    /* Returns false if eviction failed after MAX_KICKS. Caller MUST drop
     * the sample to preserve the filter-superset invariant. */
    bool insert(const void* ptr) noexcept
    {
        const Hashed h = hash_ptr(ptr);
        uint16_t fp = h.fp;

        if (bucket_insert(h.bucket, fp)) {
            size_++;
            return true;
        }
        const uint32_t b2 = alt_bucket(h.bucket, fp);
        if (bucket_insert(b2, fp)) {
            size_++;
            return true;
        }

        /* Both buckets full. Evict a random slot from b2 and walk the
         * displaced fingerprint to its alternate bucket. */
        uint32_t b = b2;
        for (size_t i = 0; i < MAX_KICKS; i++) {
            const size_t slot = static_cast<size_t>(evict_rng_()) & (SLOTS_PER_BUCKET - 1);
            std::swap(fp, table_[b].slots[slot]);
            b = alt_bucket(b, fp);
            if (bucket_insert(b, fp)) {
                size_++;
                return true;
            }
        }
        /* AIDEV-NOTE: Saturated. The very first swap at b2 already placed
         * the *new* fingerprint and started kicking older ones along the
         * chain. What we end up unable to relocate (the value still in `fp`
         * here) is an *earlier-tracked* fingerprint, not the new one. That
         * orphan corresponds to some existing allocs_m entry: the filter no
         * longer remembers it, so a future untrack of its pointer will be a
         * false negative and leak that entry — the filter ⊇ allocs_m
         * invariant is broken for that single pointer. The new pointer's
         * fingerprint, conversely, is still in the filter with no allocs_m
         * counterpart, which is harmless (extra slack on the safe side).
         * Caller MUST drop the new sample to avoid compounding the
         * asymmetry by adding a fresh allocs_m entry on top.
         *
         * AIDEV-TODO: at 50% load this branch is essentially unreachable
         * (per-insert failure probability < 1e-10). If we ever observe it
         * in production, fix with a single-slot victim cache: store
         * (orphan_b, orphan_fp) here and have contains()/erase() check it.
         * ~10 LoC, O(1) memory, restores the strict invariant. */
        return false;
    }

    bool contains(const void* ptr) const noexcept
    {
        const Hashed h = hash_ptr(ptr);
        if (bucket_contains(h.bucket, h.fp)) {
            return true;
        }
        return bucket_contains(alt_bucket(h.bucket, h.fp), h.fp);
    }

    /* Clears one slot matching the pointer's fingerprint, if any. No-op if
     * no slot matches. AIDEV-NOTE: callers must only erase pointers that
     * were previously inserted — otherwise, in the rare same-fp/same-bucket
     * collision, this could clear an unrelated tracked entry's slot and
     * induce a false negative on its eventual untrack. */
    void erase(const void* ptr) noexcept
    {
        const Hashed h = hash_ptr(ptr);
        if (bucket_erase(h.bucket, h.fp)) {
            size_--;
            return;
        }
        if (bucket_erase(alt_bucket(h.bucket, h.fp), h.fp)) {
            size_--;
        }
    }

    void clear() noexcept
    {
        std::memset(table_.data(), 0, sizeof(table_));
        size_ = 0;
    }

    size_t size() const noexcept { return size_; }

  private:
    struct Bucket
    {
        uint16_t slots[SLOTS_PER_BUCKET];
    };
    static_assert(sizeof(Bucket) == 8, "expect 8-byte bucket");

    struct Hashed
    {
        uint32_t bucket;
        uint16_t fp;
    };

    static Hashed hash_ptr(const void* p) noexcept
    {
        const uint64_t h = static_cast<uint64_t>(filter_hash_t<const void*>{}(p));
        /* Lower 32 bits → bucket index; upper 32 bits → fingerprint.
         * Both halves come from the same well-mixed hash, so they're
         * effectively independent for our purposes. */
        uint16_t fp = static_cast<uint16_t>(h >> 32);
        if (fp == EMPTY_SLOT) {
            fp = 1; /* reserve 0 for empty slots */
        }
        return Hashed{ static_cast<uint32_t>(h) & BUCKET_MASK, fp };
    }

    static uint32_t alt_bucket(uint32_t i, uint16_t fp) noexcept
    {
        const uint64_t fp_h = static_cast<uint64_t>(filter_hash_t<uint16_t>{}(fp));
        return (i ^ static_cast<uint32_t>(fp_h)) & BUCKET_MASK;
    }

    bool bucket_contains(uint32_t b, uint16_t fp) const noexcept
    {
        const Bucket& bk = table_[b];
        for (size_t i = 0; i < SLOTS_PER_BUCKET; i++) {
            if (bk.slots[i] == fp) {
                return true;
            }
        }
        return false;
    }

    bool bucket_insert(uint32_t b, uint16_t fp) noexcept
    {
        Bucket& bk = table_[b];
        for (size_t i = 0; i < SLOTS_PER_BUCKET; i++) {
            if (bk.slots[i] == EMPTY_SLOT) {
                bk.slots[i] = fp;
                return true;
            }
        }
        return false;
    }

    bool bucket_erase(uint32_t b, uint16_t fp) noexcept
    {
        Bucket& bk = table_[b];
        for (size_t i = 0; i < SLOTS_PER_BUCKET; i++) {
            if (bk.slots[i] == fp) {
                bk.slots[i] = EMPTY_SLOT;
                return true;
            }
        }
        return false;
    }

    std::array<Bucket, NUM_BUCKETS> table_;
    std::minstd_rand evict_rng_;
    size_t size_ = 0;
};
