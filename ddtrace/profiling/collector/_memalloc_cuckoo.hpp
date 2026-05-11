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
 * Saturation handling: when the eviction chain can't terminate within
 * MAX_KICKS, the displaced fingerprint is parked in a single-slot victim
 * cache (instead of being orphaned). contains() and erase() consult both
 * the table and the victim. This restores the strict superset invariant
 * for the first saturation. If a SECOND saturation occurs while the
 * victim is still occupied, insert() returns false WITHOUT mutating the
 * table (we refuse before starting the eviction chain), and the caller
 * MUST drop the sample. At our 50% load both events are essentially
 * unreachable; the victim closes the rare-but-nonzero leak window of the
 * naive cuckoo filter.
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
        /* AIDEV-NOTE: Eviction chain didn't terminate within MAX_KICKS.
         * The new fingerprint was successfully placed at the start of the
         * chain (b2[slot]); what we couldn't relocate (still in `fp` here)
         * is an *earlier-tracked* fingerprint. Park it in the victim slot
         * along with `b` (one of its two valid buckets) so future
         * contains()/erase() can still locate it. This preserves the
         * strict superset invariant. At 50% load, reaching this branch
         * has probability < 1e-10 per insert; reaching it with the victim
         * already occupied is effectively impossible. */
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
        /* AIDEV-NOTE: A fingerprint's two valid buckets are (b, alt(b, fp))
         * for any b in the pair. If the victim's fp matches and its stored
         * bucket equals either of the query's two candidate buckets, the
         * victim represents a logically-present entry for this ptr. */
        return victim_occupied_ && victim_fp_ == h.fp && (victim_bucket_ == h.bucket || victim_bucket_ == b2);
    }

    /* Clears one slot matching the pointer's fingerprint, if any. Checks
     * the table first, then the victim. AIDEV-NOTE: callers must only
     * erase pointers that were previously inserted — otherwise, in the
     * rare same-fp/same-bucket-pair collision, this could clear an
     * unrelated tracked entry and induce a false negative on its
     * eventual untrack. */
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
