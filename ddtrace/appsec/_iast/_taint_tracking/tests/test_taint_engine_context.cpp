#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "context/taint_engine_context.h"
#include "taint_tracking/taint_range.h"
#include "taint_tracking/tainted_object.h"

namespace py = pybind11;

namespace {
static inline long
refcnt(PyObject* o)
{
    return o ? Py_REFCNT(o) : 0;
}

static TaintedObjectPtr
make_tainted_with_one_range(size_t len)
{
    auto to = std::make_shared<TaintedObject>();
    TaintRangeRefs ranges;
    ranges.reserve(1);
    auto src = Source(std::string("param"), std::string("value"), OriginType::PARAMETER);
    auto tr = std::make_shared<TaintRange>(0, static_cast<RANGE_LENGTH>(len), std::move(src), 0);
    ranges.push_back(tr);
    to->set_values(std::move(ranges));
    return to;
}
}

class ApplicationContextTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // AIDEV-NOTE: Global interpreter is managed by tests/pyenv_env.cpp
        taint_engine_context = std::make_unique<TaintEngineContext>();
        taint_engine_context->clear_all_request_context_slots();
    }

    void TearDown() override
    {
        // Ensure we don't leak a running Python interpreter into subsequent tests
        taint_engine_context.reset();
    }
};

TEST_F(ApplicationContextTest, GetCurrentContextMap_ListScan_NoRefcountLeak_WithMatch)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
    ASSERT_NE(tx_map, nullptr);

    py::str a("a");
    py::str b("b");
    py::str tainted_str("TAINT");
    // Register tainted_str into current map
    auto to = make_tainted_with_one_range(tainted_str.attr("__len__")().cast<size_t>());
    set_tainted_object(tainted_str.ptr(), to, tx_map);

    py::list lst;
    lst.append(a);
    lst.append(tainted_str);
    lst.append(b);

    const long lst_before = refcnt(lst.ptr());
    const long a_before = refcnt(a.ptr());
    const long b_before = refcnt(b.ptr());
    const long t_before = refcnt(tainted_str.ptr());

    auto m = taint_engine_context->get_tainted_object_map(lst.ptr());
    ASSERT_NE(m, nullptr);

    ASSERT_EQ(refcnt(lst.ptr()), lst_before);
    ASSERT_EQ(refcnt(a.ptr()), a_before);
    ASSERT_EQ(refcnt(b.ptr()), b_before);
    ASSERT_EQ(refcnt(tainted_str.ptr()), t_before);
}

TEST_F(ApplicationContextTest, GetCurrentContextMap_ListScan_NoRefcountLeak_NoMatch)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());

    py::str a("a");
    py::str b("b");
    py::str c("c");
    py::list lst;
    lst.append(a);
    lst.append(b);
    lst.append(c);

    const long lst_before = refcnt(lst.ptr());
    const long a_before = refcnt(a.ptr());
    const long b_before = refcnt(b.ptr());
    const long c_before = refcnt(c.ptr());

    auto m = taint_engine_context->get_tainted_object_map(lst.ptr());
    ASSERT_EQ(m, nullptr);

    ASSERT_EQ(refcnt(lst.ptr()), lst_before);
    ASSERT_EQ(refcnt(a.ptr()), a_before);
    ASSERT_EQ(refcnt(b.ptr()), b_before);
    ASSERT_EQ(refcnt(c.ptr()), c_before);
}

TEST_F(ApplicationContextTest, GetCurrentContextMap_TupleScan_NoRefcountLeak_WithMatch)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
    ASSERT_NE(tx_map, nullptr);

    py::str x("x");
    py::str y("y");
    py::str tainted_str("TAINT");
    auto to = make_tainted_with_one_range(tainted_str.attr("__len__")().cast<size_t>());
    set_tainted_object(tainted_str.ptr(), to, tx_map);

    py::tuple tup(3);
    PyTuple_SET_ITEM(tup.ptr(), 0, x.inc_ref().ptr());
    PyTuple_SET_ITEM(tup.ptr(), 1, tainted_str.inc_ref().ptr());
    PyTuple_SET_ITEM(tup.ptr(), 2, y.inc_ref().ptr());

    const long tup_before = refcnt(tup.ptr());
    const long x_before = refcnt(x.ptr());
    const long y_before = refcnt(y.ptr());
    const long t_before = refcnt(tainted_str.ptr());

    auto m = taint_engine_context->get_tainted_object_map(tup.ptr());
    ASSERT_NE(m, nullptr);

    ASSERT_EQ(refcnt(tup.ptr()), tup_before);
    ASSERT_EQ(refcnt(x.ptr()), x_before);
    ASSERT_EQ(refcnt(y.ptr()), y_before);
    ASSERT_EQ(refcnt(tainted_str.ptr()), t_before);
}

TEST_F(ApplicationContextTest, GetCurrentContextMap_DictScan_NoRefcountLeak_WithMatch)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
    ASSERT_NE(tx_map, nullptr);

    py::str key1("k1");
    py::str key2("k2");
    py::str val1("v1");
    py::str tainted_val("TAINT");
    auto to = make_tainted_with_one_range(tainted_val.attr("__len__")().cast<size_t>());
    set_tainted_object(tainted_val.ptr(), to, tx_map);

    py::dict d;
    d[key1] = tainted_val;
    d[key2] = val1;

    const long d_before = refcnt(d.ptr());
    const long k1_before = refcnt(key1.ptr());
    const long k2_before = refcnt(key2.ptr());
    const long v1_before = refcnt(val1.ptr());
    const long tv_before = refcnt(tainted_val.ptr());

    auto m = taint_engine_context->get_tainted_object_map(d.ptr());
    ASSERT_NE(m, nullptr);

    ASSERT_EQ(refcnt(d.ptr()), d_before);
    ASSERT_EQ(refcnt(key1.ptr()), k1_before);
    ASSERT_EQ(refcnt(key2.ptr()), k2_before);
    ASSERT_EQ(refcnt(val1.ptr()), v1_before);
    ASSERT_EQ(refcnt(tainted_val.ptr()), tv_before);
}

TEST_F(ApplicationContextTest, GetCurrentContextMap_DictScan_NoRefcountLeak_NoMatch)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());

    py::str key1("k1");
    py::str key2("k2");
    py::str val1("v1");
    py::str val2("v2");

    py::dict d;
    d[key1] = val1;
    d[key2] = val2;

    const long d_before = refcnt(d.ptr());
    const long k1_before = refcnt(key1.ptr());
    const long k2_before = refcnt(key2.ptr());
    const long v1_before = refcnt(val1.ptr());
    const long v2_before = refcnt(val2.ptr());

    auto m = taint_engine_context->get_tainted_object_map(d.ptr());
    ASSERT_EQ(m, nullptr);

    ASSERT_EQ(refcnt(d.ptr()), d_before);
    ASSERT_EQ(refcnt(key1.ptr()), k1_before);
    ASSERT_EQ(refcnt(key2.ptr()), k2_before);
    ASSERT_EQ(refcnt(val1.ptr()), v1_before);
    ASSERT_EQ(refcnt(val2.ptr()), v2_before);
}

TEST_F(ApplicationContextTest, CreateTwoContextsAndRetrieveByIndex)
{
    // Create first context
    auto idx1 = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx1.has_value());
    auto m1 = taint_engine_context->get_tainted_object_map_by_ctx_id(*idx1);
    ASSERT_NE(m1, nullptr);

    // Create second context, should be a different slot/map
    auto idx2 = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx2.has_value());
    ASSERT_NE(*idx1, *idx2);
    auto m2 = taint_engine_context->get_tainted_object_map_by_ctx_id(*idx2);
    ASSERT_NE(m2, nullptr);
    ASSERT_NE(m1, m2);
}

TEST_F(ApplicationContextTest, ClearSpecificMap)
{
    auto idx = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx.has_value());
    auto m = taint_engine_context->get_tainted_object_map_by_ctx_id(*idx);
    ASSERT_NE(m, nullptr);

    taint_engine_context->finish_request_context(*idx);
    auto after = taint_engine_context->get_tainted_object_map_by_ctx_id(*idx);
    ASSERT_EQ(after, nullptr);
}

TEST_F(ApplicationContextTest, ReuseFreedSlotOnCreate)
{
    auto idx1 = taint_engine_context->start_request_context();
    auto idx2 = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx1.has_value());
    ASSERT_TRUE(idx2.has_value());
    ASSERT_NE(*idx1, *idx2);

    // Free the first slot
    taint_engine_context->finish_request_context(*idx1);
    ASSERT_EQ(taint_engine_context->get_tainted_object_map_by_ctx_id(*idx1), nullptr);

    // Next create should reuse the first free slot (lowest index first)
    auto idx3 = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx3.has_value());
    ASSERT_EQ(*idx3, *idx1);
    auto m3 = taint_engine_context->get_tainted_object_map_by_ctx_id(*idx3);
    ASSERT_NE(m3, nullptr);
}

TEST_F(ApplicationContextTest, ClearAllContexts)
{
    // Create up to min(3, capacity) contexts
    const auto cap = taint_engine_context->debug_context_array_size();
    const auto n = cap < 3 ? cap : static_cast<size_t>(3);
    std::vector<size_t> indices;
    for (size_t i = 0; i < n; ++i) {
        auto idx = taint_engine_context->start_request_context();
        ASSERT_TRUE(idx.has_value());
        indices.push_back(*idx);
    }

    taint_engine_context->clear_all_request_context_slots();
    for (size_t i = 0; i < cap; ++i) {
        // All slots should be cleared
        auto m = taint_engine_context->get_tainted_object_map_by_ctx_id(i);
        ASSERT_EQ(m, nullptr);
    }
}

TEST_F(ApplicationContextTest, ClearTaintMapFreesContainedTaintedObjects)
{
    // Arrange: create a context and a tainted entry
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
    ASSERT_NE(tx_map, nullptr);

    // Insert a TaintedObjectPtr directly into the map with a dummy key/hash
    TaintedObjectPtr to = std::make_shared<TaintedObject>();
    std::weak_ptr<TaintedObject> w_to = to; // observe lifetime
    const uintptr_t dummy_key = 0xDEADBEEF;
    const Py_hash_t dummy_hash = 0;
    tx_map->insert({ dummy_key, std::make_pair(dummy_hash, to) });

    // Drop our local strong reference so the map is the sole owner
    to.reset();

    // Sanity: ensure the weak_ptr is not expired yet
    ASSERT_FALSE(w_to.expired());

    // Act: clear the taint map slot
    taint_engine_context->finish_request_context(ctx_id);

    // Assert: the TaintedObject has been destroyed (no remaining strong refs)
    ASSERT_TRUE(w_to.expired());
}

TEST_F(ApplicationContextTest, ClearContextsArrayFreesTaintRangeMap)
{
    // Arrange: create a context map
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;
    auto tx_map = taint_engine_context->get_tainted_object_map_by_ctx_id(ctx_id);
    ASSERT_NE(tx_map, nullptr);

    // Take a weak reference to the map itself
    std::weak_ptr<TaintedObjectMapType> w_map = tx_map;

    // Drop local strong reference; request_context_slots should be the only owner now
    tx_map.reset();
    ASSERT_FALSE(w_map.expired());

    // Act: clear all contexts (sets slot to nullptr and releases the shared_ptr)
    taint_engine_context->clear_all_request_context_slots();

    // Assert: the map is destroyed (no remaining strong refs)
    ASSERT_TRUE(w_map.expired());
}

TEST_F(ApplicationContextTest, FinishRequestContextWithInvalidIndexIsNoop)
{
    const auto cap = taint_engine_context->debug_context_array_size();
    // Act: finish an out-of-range index; should not crash
    taint_engine_context->finish_request_context(cap + 10);
    // Assert: out-of-range map lookup returns nullptr
    auto m = taint_engine_context->get_tainted_object_map_by_ctx_id(cap + 10);
    ASSERT_EQ(m, nullptr);
}

TEST_F(ApplicationContextTest, FinishRequestContextTwiceIsNoop)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto id = *idx_opt;
    ASSERT_NE(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);

    // First finish clears the slot
    taint_engine_context->finish_request_context(id);
    ASSERT_EQ(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);

    // Second finish is a no-op
    taint_engine_context->finish_request_context(id);
    ASSERT_EQ(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);
}

TEST_F(ApplicationContextTest, GetTaintedObjectMapByInvalidIndexReturnsNull)
{
    const auto cap = taint_engine_context->debug_context_array_size();
    auto m1 = taint_engine_context->get_tainted_object_map_by_ctx_id(cap);
    auto m2 = taint_engine_context->get_tainted_object_map_by_ctx_id(cap + 123);
    ASSERT_EQ(m1, nullptr);
    ASSERT_EQ(m2, nullptr);
}

TEST_F(ApplicationContextTest, StartUntilCapacityThenNextReturnsNullopt)
{
    const auto cap = taint_engine_context->debug_context_array_size();
    std::vector<size_t> ids;
    ids.reserve(cap);
    for (size_t i = 0; i < cap; ++i) {
        auto idx = taint_engine_context->start_request_context();
        ASSERT_TRUE(idx.has_value());
        ids.push_back(*idx);
    }
    // Next start should fail when at capacity
    auto extra = taint_engine_context->start_request_context();
    ASSERT_FALSE(extra.has_value());

    // After clearing, a new start should succeed
    taint_engine_context->clear_all_request_context_slots();
    auto idx_after_clear = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_after_clear.has_value());
}

TEST_F(ApplicationContextTest, ClearAllIsIdempotent)
{
    // Call clear on an already empty array
    taint_engine_context->clear_all_request_context_slots();
    // Create one context and clear twice
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto id = *idx_opt;
    ASSERT_NE(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);

    taint_engine_context->clear_all_request_context_slots();
    ASSERT_EQ(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);
    // Second call should be harmless
    taint_engine_context->clear_all_request_context_slots();
    ASSERT_EQ(taint_engine_context->get_tainted_object_map_by_ctx_id(id), nullptr);
}
