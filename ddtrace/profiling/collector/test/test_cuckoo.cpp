/* Unit tests for CuckooFilterImpl.
 *
 * Self-contained — no gtest dependency. Uses <cassert>; failures abort()
 * the process with the line number, which is plenty when read in CI logs.
 *
 * Build & run locally:
 *   clang++ -std=c++20 -O2 -I.. -DDONT_COMPILE_ABSEIL test_cuckoo.cpp \
 *     -o /tmp/test_cuckoo && /tmp/test_cuckoo
 *
 * Wired into CMakeLists.txt under -DBUILD_TESTING=ON; CTest discovers it.
 */

#undef NDEBUG /* assert() must always fire here, regardless of build type */
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include <unordered_set>
#include <vector>

#include "_memalloc_cuckoo.hpp"

namespace {

/* Small helper: a deterministic stream of distinct, well-distributed
 * "pointer" values for testing. We don't actually dereference them; the
 * filter only cares about their hash. */
class FakePtrSource
{
  public:
    explicit FakePtrSource(uint64_t seed)
      : rng_(seed)
    {
    }
    const void* next()
    {
        /* Drop the bottom 4 bits to mimic 16-byte alignment of real
         * malloc output, exercising hash robustness against low-entropy
         * pointer bottoms. */
        uintptr_t v = static_cast<uintptr_t>(rng_()) & ~uintptr_t{ 0xF };
        return reinterpret_cast<const void*>(v);
    }

  private:
    std::mt19937_64 rng_;
};

/* ---------- Test 1: empty filter never reports membership ---------- */
void
test_empty_never_contains()
{
    CuckooFilter f;
    FakePtrSource src(1);
    for (int i = 0; i < 1000; i++) {
        assert(!f.contains(src.next()));
    }
    assert(f.size() == 0);
    std::printf("  test_empty_never_contains: PASS\n");
}

/* ---------- Test 2: insert then contains returns true ---------- */
void
test_insert_then_contains()
{
    CuckooFilter f;
    FakePtrSource src(2);
    std::vector<const void*> ptrs;
    for (int i = 0; i < 1000; i++) {
        ptrs.push_back(src.next());
    }
    for (auto p : ptrs) {
        assert(f.insert(p));
    }
    for (auto p : ptrs) {
        assert(f.contains(p));
    }
    assert(f.size() == ptrs.size());
    std::printf("  test_insert_then_contains: PASS\n");
}

/* ---------- Test 3: erase removes one matching slot ---------- */
void
test_insert_erase_round_trip()
{
    CuckooFilter f;
    FakePtrSource src(3);
    std::vector<const void*> ptrs;
    for (int i = 0; i < 1000; i++) {
        ptrs.push_back(src.next());
    }
    for (auto p : ptrs) {
        assert(f.insert(p));
    }
    for (auto p : ptrs) {
        f.erase(p);
    }
    /* size_ may be slightly above zero if there were any same-fp/same-bucket
     * collisions where erase cleared one and the other remained, but for
     * 1000 random keys against a 32K-bucket filter the expected residual is
     * essentially zero. Allow some slack for randomness. */
    assert(f.size() <= 5);
    std::printf("  test_insert_erase_round_trip: PASS (residual=%zu)\n", f.size());
}

/* ---------- Test 4: erase on never-inserted ptr is benign ---------- */
void
test_erase_unknown_no_op()
{
    CuckooFilter f;
    FakePtrSource src(4);
    /* Insert 100 ptrs */
    std::vector<const void*> inserted;
    for (int i = 0; i < 100; i++) {
        inserted.push_back(src.next());
        assert(f.insert(inserted.back()));
    }
    const size_t before = f.size();

    /* Try to erase 100 different ptrs we never inserted */
    FakePtrSource src2(0xDEAD);
    for (int i = 0; i < 100; i++) {
        f.erase(src2.next());
    }

    /* Most of these are no-ops. A few might have collided with inserted
     * fingerprints and decremented size_, but it's bounded by FPR ~ 1/8192
     * across 100 tries: expected < 0.02 false positives. Allow generous
     * slack. */
    assert(f.size() >= before - 2);

    /* All originally-inserted ptrs should still be findable (modulo any
     * unfortunate collision-driven erasure). */
    size_t still_found = 0;
    for (auto p : inserted) {
        if (f.contains(p)) {
            still_found++;
        }
    }
    assert(still_found >= inserted.size() - 2);
    std::printf("  test_erase_unknown_no_op: PASS\n");
}

/* ---------- Test 5: clear() resets state ---------- */
void
test_clear_resets()
{
    CuckooFilter f;
    FakePtrSource src(5);
    std::vector<const void*> ptrs;
    for (int i = 0; i < 5000; i++) {
        ptrs.push_back(src.next());
        assert(f.insert(ptrs.back()));
    }
    assert(f.size() == 5000);

    f.clear();
    assert(f.size() == 0);
    /* contains() should now return false for everything. Some FPs are
     * possible against a fresh filter, but at FPR ~ 1/8192 across 5000
     * queries the expected count is < 1. Allow tiny slack. */
    size_t residual = 0;
    for (auto p : ptrs) {
        if (f.contains(p)) {
            residual++;
        }
    }
    assert(residual <= 2);
    std::printf("  test_clear_resets: PASS (residual=%zu)\n", residual);
}

/* ---------- Test 6: heavy random churn at production load ---------- */
void
test_random_churn_no_false_negatives()
{
    CuckooFilter f;
    FakePtrSource src(6);
    /* Build a working set up to ~50% load (the production worst case). */
    constexpr size_t TARGET = 60000;
    std::unordered_set<const void*> live;
    live.reserve(TARGET);
    while (live.size() < TARGET) {
        const void* p = src.next();
        if (live.insert(p).second) {
            assert(f.insert(p) && "filter rejected an insert at 50% load");
        }
    }
    assert(f.size() >= TARGET); /* size_ may exceed TARGET if duplicate fps occurred */

    /* No false negatives: every live ptr must be reported as present. */
    for (auto p : live) {
        assert(f.contains(p) && "false negative on a live key — corruption!");
    }

    /* Erase half, verify remaining half still present. */
    size_t erased = 0;
    auto it = live.begin();
    while (it != live.end() && erased < TARGET / 2) {
        f.erase(*it);
        it = live.erase(it);
        erased++;
    }
    for (auto p : live) {
        assert(f.contains(p) && "false negative after partial erase");
    }
    std::printf("  test_random_churn_no_false_negatives: PASS\n");
}

/* ---------- Test 7: tiny filter, normal inserts up to capacity ---------- */
void
test_tiny_filter_fills_to_capacity()
{
    /* 2 buckets × 2 slots × max-4-kicks: 4 slots total. */
    CuckooFilterImpl<2, 2, 4> tiny;
    FakePtrSource src(7);

    /* Insert up to 4 ptrs — each should succeed (normal inserts fill
     * empty slots). */
    std::vector<const void*> ptrs;
    for (int i = 0; i < 4; i++) {
        ptrs.push_back(src.next());
        const bool ok = tiny.insert(ptrs.back());
        assert(ok && "inserts within slot capacity must succeed");
    }
    assert(tiny.size() == 4);
    std::printf("  test_tiny_filter_fills_to_capacity: PASS\n");
}

/* ---------- Test 8: tiny filter, saturation engages the victim cache ---------- */
void
test_tiny_filter_saturation_uses_victim()
{
    CuckooFilterImpl<2, 2, 4> tiny;
    FakePtrSource src(8);

    /* Fill to capacity. */
    std::vector<const void*> ptrs;
    for (int i = 0; i < 4; i++) {
        ptrs.push_back(src.next());
        assert(tiny.insert(ptrs.back()));
    }

    /* Now the (b1, b2) pair is full. Any further insert triggers the
     * eviction chain. With only 4 slots and MaxKicks=4, every kick fails
     * to find an empty slot and the orphan ends up in the victim cache.
     * insert() must return TRUE — the entry is logically present. */
    const void* p_new = src.next();
    const bool ok = tiny.insert(p_new);
    assert(ok && "first saturation must succeed via the victim cache");
    assert(tiny.size() == 5);

    /* The new pointer must be findable. */
    assert(tiny.contains(p_new));

    /* All originally-inserted pointers must STILL be findable: the victim
     * cache absorbed the orphan from the eviction chain. This is the key
     * property the cache exists to guarantee. */
    for (auto p : ptrs) {
        assert(tiny.contains(p) && "victim cache failed: a previously-inserted entry is now missing");
    }
    std::printf("  test_tiny_filter_saturation_uses_victim: PASS\n");
}

/* ---------- Test 9: second saturation refused while victim occupied ------- */
void
test_tiny_filter_second_saturation_refused()
{
    CuckooFilterImpl<2, 2, 4> tiny;
    FakePtrSource src(9);

    /* Fill + first saturation (occupies the victim). */
    for (int i = 0; i < 5; i++) {
        const bool ok = tiny.insert(src.next());
        assert(ok);
    }
    /* Snapshot size_ before the refused insert. */
    const size_t size_before = tiny.size();

    /* A 6th insert hits both buckets full AND victim occupied. Must
     * return false WITHOUT touching size_ or the table contents. */
    const bool ok = tiny.insert(src.next());
    assert(!ok && "second saturation must be refused");
    assert(tiny.size() == size_before && "refused insert must not change size_");
    std::printf("  test_tiny_filter_second_saturation_refused: PASS\n");
}

/* ---------- Test 10: erase reaches the victim slot ---------- */
void
test_tiny_filter_erase_via_victim()
{
    CuckooFilterImpl<2, 2, 4> tiny;
    FakePtrSource src(10);

    std::vector<const void*> ptrs;
    for (int i = 0; i < 5; i++) {
        ptrs.push_back(src.next());
        assert(tiny.insert(ptrs.back()));
    }
    /* size_ is 5 (4 in table, 1 in victim — we don't know which ptr is
     * where, but the invariant is all 5 must be findable and erasable). */
    assert(tiny.size() == 5);

    /* Erase every pointer. After all erasures, every contains() should
     * return false (modulo the rare same-fp/same-bucket aliasing case
     * which is plausible at 5 entries in a 4-slot filter; allow tiny
     * residual). */
    for (auto p : ptrs) {
        tiny.erase(p);
    }
    size_t residual_found = 0;
    for (auto p : ptrs) {
        if (tiny.contains(p)) {
            residual_found++;
        }
    }
    assert(residual_found <= 1);

    /* After clearing, the victim slot should be free again so we can
     * trigger another saturation. */
    tiny.clear();
    assert(tiny.size() == 0);
    FakePtrSource src2(11);
    for (int i = 0; i < 5; i++) {
        assert(tiny.insert(src2.next()));
    }
    /* We made it through another saturation, meaning the victim was
     * properly reset by clear(). */
    std::printf("  test_tiny_filter_erase_via_victim: PASS\n");
}

} // namespace

int
main()
{
    std::printf("Running CuckooFilter tests...\n");
    test_empty_never_contains();
    test_insert_then_contains();
    test_insert_erase_round_trip();
    test_erase_unknown_no_op();
    test_clear_resets();
    test_random_churn_no_false_negatives();
    test_tiny_filter_fills_to_capacity();
    test_tiny_filter_saturation_uses_victim();
    test_tiny_filter_second_saturation_refused();
    test_tiny_filter_erase_via_victim();
    std::printf("All CuckooFilter tests passed.\n");
    return 0;
}
