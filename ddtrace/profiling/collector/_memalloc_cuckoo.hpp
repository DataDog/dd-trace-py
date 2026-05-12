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

/* Cuckoo filter for fast-reject on the heap profiler free path.
 *
 * ~99% of frees aren't sampled. The filter answers "is this pointer
 * sampled?" in ~10-15 cycles by probing two buckets of 16-bit fingerprints,
 * versus 50-100 cycles for an allocs_m miss.
 *
 * Invariant: filter ⊇ allocs_m keys. False positives fall through to an
 * empty extract; false negatives would leak heap-tracker state.
 *
 * Saturation: when the eviction chain can't terminate within MAX_KICKS,
 * the displaced fingerprint is parked in a single-slot victim cache so the
 * superset invariant survives. contains()/erase() consult both. A second
 * back-to-back saturation while the victim is occupied returns false from
 * insert() without mutating the table; the caller must drop the sample.
 * At 50% load both events have probability < 1e-10 per insert.
 *
 * Sizing: 32768 buckets × 4 slots × 2 bytes = 256 KB (fits in L2). Load
 * factor caps at ~50% because the heap tracker stops at
 * TRACEBACK_ARRAY_MAX_COUNT (65535) live entries. With f=16, b=4 the
 * standard false-positive-rate formula 2b/2^f ≈ 1/8192 (~0.012%) per absent-key probe.
 *
 * Hashing: standard partial-key cuckoo (Fan et al. 2014):
 *   h1 = hash(ptr) mod NUM_BUCKETS
 *   h2 = h1 ^ (hash(fp) mod NUM_BUCKETS)
 * XOR is involutive, so deletion works without storing the full key.
 *
 * Not thread-safe (GIL-protected like allocs_m). PEP 703 will require
 * migrating both to atomics together.
 */
/* Template parameters expose the filter's geometry so tests can instantiate
 * tiny variants (e.g. CuckooFilterImpl<2, 2, 4>) and exercise the
 * saturation/victim-cache paths deterministically. Production code uses
 * the default parameters via the `CuckooFilter` alias below. */
template<size_t NumBuckets = (1u << 15) /* 32768 */, size_t SlotsPerBucket = 4, size_t MaxKicks = 500>
class CuckooFilterImpl
{
    static_assert((NumBuckets & (NumBuckets - 1)) == 0, "NumBuckets must be a power of 2");
    static_assert(SlotsPerBucket > 0 && (SlotsPerBucket & (SlotsPerBucket - 1)) == 0,
                  "SlotsPerBucket must be a power of 2");

  public:
    static constexpr size_t NUM_BUCKETS = NumBuckets;
    static constexpr size_t SLOTS_PER_BUCKET = SlotsPerBucket;
    static constexpr size_t MAX_KICKS = MaxKicks;
    static constexpr uint32_t BUCKET_MASK = static_cast<uint32_t>(NumBuckets - 1);
    static constexpr uint16_t EMPTY_SLOT = 0;

    CuckooFilterImpl() noexcept
      : evict_rng_(0x9e3779b9U)
    {
        clear();
    }

    /* Returns false only if a second saturation would occur while the victim
     * slot is already in use. In that case the table is left untouched and
     * the caller MUST drop the sample to preserve filter ⊇ allocs_m. */
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

        /* Both buckets are full; eviction is the only way to place fp.
         * If the victim slot is already occupied, refuse BEFORE we
         * disturb the table — otherwise an unsuccessful chain would
         * orphan another original entry beyond our recovery. */
        if (victim_occupied_) {
            return false;
        }

        /* Evict a random slot from b2 and walk the displaced fingerprint
         * to its alternate bucket. */
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
        /* Eviction saturated. The new fingerprint already landed at the
         * start of the chain; `fp` here is an earlier-tracked fingerprint
         * we couldn't relocate. Park it with one of its valid buckets in
         * the victim slot so contains()/erase() still find it, preserving
         * the superset invariant. */
        victim_fp_ = fp;
        victim_bucket_ = b;
        victim_occupied_ = true;
        size_++;
        return true;
    }

    bool contains(const void* ptr) const noexcept
    {
        const Hashed h = hash_ptr(ptr);
        if (bucket_contains(h.bucket, h.fp)) {
            return true;
        }
        const uint32_t b2 = alt_bucket(h.bucket, h.fp);
        if (bucket_contains(b2, h.fp)) {
            return true;
        }
        /* Victim hit only if its fp matches and its parked bucket is one
         * of this ptr's two candidate buckets. */
        return victim_occupied_ && victim_fp_ == h.fp && (victim_bucket_ == h.bucket || victim_bucket_ == b2);
    }

    /* Clears one slot matching the pointer's fingerprint (table first,
     * then victim). Caller must only erase previously-inserted pointers:
     * a same-fp/same-bucket collision could otherwise clear an unrelated
     * tracked entry and induce a false negative on its later untrack. */
    void erase(const void* ptr) noexcept
    {
        const Hashed h = hash_ptr(ptr);
        if (bucket_erase(h.bucket, h.fp)) {
            size_--;
            return;
        }
        const uint32_t b2 = alt_bucket(h.bucket, h.fp);
        if (bucket_erase(b2, h.fp)) {
            size_--;
            return;
        }
        if (victim_occupied_ && victim_fp_ == h.fp && (victim_bucket_ == h.bucket || victim_bucket_ == b2)) {
            victim_occupied_ = false;
            size_--;
        }
    }

    void clear() noexcept
    {
        std::memset(table_.data(), 0, sizeof(table_));
        victim_occupied_ = false;
        size_ = 0;
    }

    size_t size() const noexcept { return size_; }

  private:
    struct Bucket
    {
        uint16_t slots[SLOTS_PER_BUCKET];
    };
    static_assert(sizeof(Bucket) == sizeof(uint16_t) * SLOTS_PER_BUCKET, "Bucket must be tightly packed");

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
    /* Single-slot victim cache for fingerprints displaced by a saturated
     * eviction chain. See class-level docs and insert() for semantics. */
    uint16_t victim_fp_ = 0;
    uint32_t victim_bucket_ = 0;
    bool victim_occupied_ = false;
};

/* Default-parameter alias used by the heap profiler. Tests instantiate
 * CuckooFilterImpl<...> directly with smaller geometry. */
using CuckooFilter = CuckooFilterImpl<>;
