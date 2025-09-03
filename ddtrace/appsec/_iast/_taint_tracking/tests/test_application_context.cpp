#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "context/application_context.h"

namespace py = pybind11;

class ApplicationContextTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        if (!Py_IsInitialized()) {
            py::initialize_interpreter();
        }
        application_context = std::make_unique<ApplicationContext>();
        application_context->clear_contexts_array();
    }
    void TearDown() override
    {
        // Ensure we don't leak a running Python interpreter into subsequent tests
        application_context.reset();
        if (Py_IsInitialized()) {
            py::finalize_interpreter();
        }
    }
};

TEST_F(ApplicationContextTest, CreateTwoContextsAndRetrieveByIndex)
{
    // Create first context
    auto idx1 = application_context->create_context_array();
    ASSERT_TRUE(idx1.has_value());
    auto m1 = application_context->get_taint_map_by_ctx_id(*idx1);
    ASSERT_NE(m1, nullptr);

    // Create second context, should be a different slot/map
    auto idx2 = application_context->create_context_array();
    ASSERT_TRUE(idx2.has_value());
    ASSERT_NE(*idx1, *idx2);
    auto m2 = application_context->get_taint_map_by_ctx_id(*idx2);
    ASSERT_NE(m2, nullptr);
    ASSERT_NE(m1, m2);
}

TEST_F(ApplicationContextTest, ClearSpecificMap)
{
    auto idx = application_context->create_context_array();
    ASSERT_TRUE(idx.has_value());
    auto m = application_context->get_taint_map_by_ctx_id(*idx);
    ASSERT_NE(m, nullptr);

    application_context->clear_taint_map(*idx);
    auto after = application_context->get_taint_map_by_ctx_id(*idx);
    ASSERT_EQ(after, nullptr);}

TEST_F(ApplicationContextTest, ReuseFreedSlotOnCreate)
{
    auto idx1 = application_context->create_context_array();
    auto idx2 = application_context->create_context_array();
    ASSERT_TRUE(idx1.has_value());
    ASSERT_TRUE(idx2.has_value());
    ASSERT_NE(*idx1, *idx2);

    // Free the first slot
    application_context->clear_taint_map(*idx1);
    ASSERT_EQ(application_context->get_taint_map_by_ctx_id(*idx1), nullptr);

    // Next create should reuse the first free slot (lowest index first)
    auto idx3 = application_context->create_context_array();
    ASSERT_TRUE(idx3.has_value());
    ASSERT_EQ(*idx3, *idx1);
    auto m3 = application_context->get_taint_map_by_ctx_id(*idx3);
    ASSERT_NE(m3, nullptr);
}

TEST_F(ApplicationContextTest, ClearAllContexts)
{
    // Create up to min(3, capacity) contexts
    const auto cap = application_context->capacity();
    const auto n = cap < 3 ? cap : static_cast<size_t>(3);
    std::vector<size_t> indices;
    for (size_t i = 0; i < n; ++i) {
        auto idx = application_context->create_context_array();
        ASSERT_TRUE(idx.has_value());
        indices.push_back(*idx);
    }

    application_context->clear_contexts_array();
    for (size_t i = 0; i < cap; ++i) {
        // All slots should be cleared
        auto m = application_context->get_taint_map_by_ctx_id(i);
        ASSERT_EQ(m, nullptr);
    }
}
