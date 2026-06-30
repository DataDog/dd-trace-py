// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <new>
#include <vector>

#include <echion/errors.h>

// SetAssocCache is a drop-in replacement for LRUCache (see cache.h) that
// trades the std::list + std::unordered_map (LRU) implementation for a flat,
// fixed-capacity, N-way set-associative array.
//
// Motivation: the LRU cache allocates a std::list node AND an unordered_map
// node for every stored entry, and performs a list splice on every hit. A DoE
// on the heap profiler's analogous code cache (dd-trace-py#18301) showed that a
// custom set-associative cache substantially out-performs both std::unordered_map
// and a list+map LRU on repetitive-stack workloads. This header brings the same
// design to Echion's frame cache so the two can be compared head-to-head.
//
// Organization: sets are indexed by a Fibonacci hash of the key; within a set,
// the WAYS slots are linearly scanned. On a set-full insert, FIFO eviction
// overwrites the next_evict slot. Values are held in std::unique_ptr<V> exactly
// like LRUCache, so references returned by lookup()/store() stay valid until the
// owning slot is overwritten -- callers copy out immediately (FrameStack owns
// its Frames), so transient validity is sufficient.
//
// Concurrency: like LRUCache, this is used single-threaded by the sampling
// thread; no internal locking.
template<typename K, typename V>
class SetAssocCache
{
  public:
    // Number of slots per set (associativity). 8-way (vs the #18301 2-way) to
    // cut conflict misses on the stack profiler's deep-frame working set, where
    // many hot keys can otherwise collide into the same 2-slot set and evict
    // each other despite ample total capacity.
    static constexpr size_t WAYS = 8;

    explicit SetAssocCache(size_t capacity)
    {
        // Round the requested capacity down to WAYS slots per set and up to a
        // power-of-two number of sets so the Fibonacci-hash index is a cheap shift.
        size_t requested_sets = std::max<size_t>(capacity / WAYS, size_t{ 1 });
        num_sets_ = round_up_pow2(requested_sets);
        shift_ = 64 - log2_pow2(num_sets_);
        sets_.resize(num_sets_);
    }

    Result<std::reference_wrapper<V>> lookup(const K& k)
    {
        Set& s = sets_[index(k)];
        for (size_t w = 0; w < WAYS; ++w) {
            if (s.occupied[w] && s.keys[w] == k && s.values[w]) {
                return std::reference_wrapper<V>(*s.values[w]);
            }
        }
        return ErrorKind::LookupError;
    }

    void store(const K& k, std::unique_ptr<V> v)
    {
        Set& s = sets_[index(k)];

        // Overwrite an existing entry for the same key.
        for (size_t w = 0; w < WAYS; ++w) {
            if (s.occupied[w] && s.keys[w] == k) {
                s.values[w] = std::move(v);
                return;
            }
        }

        // Fill a free slot if one exists.
        for (size_t w = 0; w < WAYS; ++w) {
            if (!s.occupied[w]) {
                s.keys[w] = k;
                s.values[w] = std::move(v);
                s.occupied[w] = true;
                return;
            }
        }

        // Set is full: FIFO eviction of the next_evict slot.
        const uint8_t w = s.next_evict;
        s.keys[w] = k;
        s.values[w] = std::move(v);
        s.occupied[w] = true;
        s.next_evict = static_cast<uint8_t>((s.next_evict + 1) % WAYS);
    }

    void clear()
    {
        for (auto& s : sets_) {
            s = Set{};
        }
    }

    void postfork_child()
    {
        // At fork, the sampling thread may have been modifying a set mid-store.
        // Running unique_ptr destructors over possibly-corrupted slots can crash.
        // Placement-new a fresh vector, abandoning the old data (intentional
        // one-time leak), mirroring LRUCache::postfork_child.
        new (&sets_) std::vector<Set>();
        sets_.resize(num_sets_);
    }

  private:
    struct Set
    {
        K keys[WAYS] = {};
        std::unique_ptr<V> values[WAYS] = {};
        bool occupied[WAYS] = { false };
        // FIFO eviction cursor: next way to overwrite when the set is full.
        uint8_t next_evict = 0;
    };

    std::vector<Set> sets_;
    size_t num_sets_ = 0;
    unsigned shift_ = 0;

    size_t index(const K& k) const
    {
        // Fibonacci hashing: multiply by 2^64/phi and take the top bits, which
        // mixes the already-composite frame key (code/lasti/firstlineno) well.
        const uint64_t h = static_cast<uint64_t>(k) * 0x9E3779B97F4A7C15ULL;
        return static_cast<size_t>(h >> shift_);
    }

    static size_t round_up_pow2(size_t n)
    {
        size_t p = 1;
        while (p < n) {
            p <<= 1;
        }
        return p;
    }

    static unsigned log2_pow2(size_t n)
    {
        unsigned l = 0;
        while ((size_t{ 1 } << l) < n) {
            ++l;
        }
        return l;
    }
};
