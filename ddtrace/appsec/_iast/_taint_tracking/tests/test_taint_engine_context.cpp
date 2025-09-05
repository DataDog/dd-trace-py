#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "context/taint_engine_context.h"
#include "taint_tracking/taint_range.h"
#include "taint_tracking/tainted_object.h"

namespace py = pybind11;

class ApplicationContextTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        if (!Py_IsInitialized()) {
            py::initialize_interpreter();
        }
        taint_engine_context = std::make_unique<TaintEngineContext>();
        taint_engine_context->clear_all_request_context_slots();
    }
    void TearDown() override
    {
        // Ensure we don't leak a running Python interpreter into subsequent tests
        taint_engine_context.reset();
        if (Py_IsInitialized()) {
            py::finalize_interpreter();
        }
    }
};

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
