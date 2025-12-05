#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "api/safe_context.h"
#include "api/safe_initializer.h"
#include "context/taint_engine_context.h"
#include "initializer/initializer.h"
#include "taint_tracking/taint_range.h"
#include "taint_tracking/tainted_object.h"

namespace py = pybind11;

/**
 * Test suite for safe wrapper functions that handle uninitialized global pointers.
 *
 * These tests verify that the safe_* wrapper functions correctly handle the case
 * when taint_engine_context or initializer are nullptr (before initialize_native_state()
 * is called), preventing segmentation faults.
 */

// ============================================================================
// Test safe_context wrapper functions
// ============================================================================

class SafeContextWrappersTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Intentionally do NOT initialize globals - test nullptr handling
        taint_engine_context.reset();
        initializer.reset();
    }

    void TearDown() override
    {
        // Clean up
        taint_engine_context.reset();
        initializer.reset();
    }
};

TEST_F(SafeContextWrappersTest, SafeGetTaintedObjectMap_ReturnsNullptr_WhenContextIsNull)
{
    py::str test_str("test");
    auto result = safe_get_tainted_object_map(test_str.ptr());
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeContextWrappersTest, SafeGetTaintedObjectMapByCtxId_ReturnsNullptr_WhenContextIsNull)
{
    auto result = safe_get_tainted_object_map_by_ctx_id(0);
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeContextWrappersTest, SafeGetTaintedObjectMapFromList_ReturnsNullptr_WhenContextIsNull)
{
    py::str str1("str1");
    py::str str2("str2");
    std::vector<PyObject*> objects = { str1.ptr(), str2.ptr() };
    auto result = safe_get_tainted_object_map_from_list_of_pyobjects(objects);
    EXPECT_EQ(result, nullptr);
}

// ============================================================================
// Test safe_initializer wrapper functions
// ============================================================================

class SafeInitializerWrappersTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Intentionally do NOT initialize globals - test nullptr handling
        taint_engine_context.reset();
        initializer.reset();
    }

    void TearDown() override
    {
        // Clean up
        taint_engine_context.reset();
        initializer.reset();
    }
};

TEST_F(SafeInitializerWrappersTest, SafeAllocateTaintRange_ReturnsNullptr_WhenInitializerIsNull)
{
    Source src("test", "value", OriginType::PARAMETER);
    auto result = safe_allocate_taint_range(0, 10, src, 0);
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeInitializerWrappersTest, SafeAllocateTaintedObject_ReturnsNullptr_WhenInitializerIsNull)
{
    auto result = safe_allocate_tainted_object();
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeInitializerWrappersTest, SafeAllocateTaintedObjectCopy_ReturnsNullptr_WhenInitializerIsNull)
{
    auto dummy = std::make_shared<TaintedObject>();
    auto result = safe_allocate_tainted_object_copy(dummy);
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeInitializerWrappersTest, SafeAllocateRangesIntoTaintObject_ReturnsNullptr_WhenInitializerIsNull)
{
    TaintRangeRefs ranges;
    auto result = safe_allocate_ranges_into_taint_object(ranges);
    EXPECT_EQ(result, nullptr);
}

TEST_F(SafeInitializerWrappersTest, SafeAllocateRangesIntoTaintObjectCopy_ReturnsNullptr_WhenInitializerIsNull)
{
    TaintRangeRefs ranges;
    auto result = safe_allocate_ranges_into_taint_object_copy(ranges);
    EXPECT_EQ(result, nullptr);
}

// ============================================================================
// Test that safe wrappers work correctly when globals ARE initialized
// ============================================================================

class SafeWrappersWithInitializationTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Initialize globals properly
        initializer = std::make_unique<Initializer>();
        taint_engine_context = std::make_unique<TaintEngineContext>();
        taint_engine_context->clear_all_request_context_slots();
    }

    void TearDown() override
    {
        if (taint_engine_context) {
            taint_engine_context->clear_all_request_context_slots();
        }
        taint_engine_context.reset();
        initializer.reset();
    }
};

TEST_F(SafeWrappersWithInitializationTest, SafeGetTaintedObjectMapByCtxId_ReturnsMap_WhenInitialized)
{
    auto idx_opt = taint_engine_context->start_request_context();
    ASSERT_TRUE(idx_opt.has_value());
    const auto ctx_id = *idx_opt;

    auto result = safe_get_tainted_object_map_by_ctx_id(ctx_id);
    EXPECT_NE(result, nullptr);
}

TEST_F(SafeWrappersWithInitializationTest, SafeAllocateTaintRange_WorksCorrectly_WhenInitialized)
{
    Source src("test_source", "test_value", OriginType::PARAMETER);
    auto result = safe_allocate_taint_range(0, 10, src, 0);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(result->start, 0);
    EXPECT_EQ(result->length, 10);
    EXPECT_EQ(result->source.name, "test_source");
    EXPECT_EQ(result->source.value, "test_value");
}

TEST_F(SafeWrappersWithInitializationTest, SafeAllocateTaintedObject_WorksCorrectly_WhenInitialized)
{
    auto result = safe_allocate_tainted_object();
    ASSERT_NE(result, nullptr);
    EXPECT_TRUE(result->get_ranges().empty());
}

TEST_F(SafeWrappersWithInitializationTest, SafeAllocateRangesIntoTaintObject_WorksCorrectly_WhenInitialized)
{
    // Create some ranges
    TaintRangeRefs ranges;
    Source src1("src1", "val1", OriginType::PARAMETER);
    Source src2("src2", "val2", OriginType::PARAMETER);

    auto range1 = safe_allocate_taint_range(0, 5, src1, 0);
    auto range2 = safe_allocate_taint_range(5, 5, src2, 0);

    ranges.push_back(range1);
    ranges.push_back(range2);

    auto result = safe_allocate_ranges_into_taint_object(ranges);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(result->get_ranges().size(), 2);
    EXPECT_EQ(result->get_ranges()[0]->source.name, "src1");
    EXPECT_EQ(result->get_ranges()[1]->source.name, "src2");
}

TEST_F(SafeWrappersWithInitializationTest, SafeAllocateRangesIntoTaintObjectCopy_WorksCorrectly_WhenInitialized)
{
    // Create some ranges
    TaintRangeRefs ranges;
    Source src("source", "value", OriginType::PARAMETER);
    auto range = safe_allocate_taint_range(0, 10, src, 0);
    ranges.push_back(range);

    auto result = safe_allocate_ranges_into_taint_object_copy(ranges);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(result->get_ranges().size(), 1);
    EXPECT_EQ(result->get_ranges()[0]->source.name, "source");

    // Verify it's a copy - original ranges should still be valid
    EXPECT_EQ(ranges.size(), 1);
    EXPECT_NE(ranges[0], nullptr);
}

// ============================================================================
// Test initialize_native_state and reset_native_state behavior
// ============================================================================

class InitializeNativeStateTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Ensure clean state
        taint_engine_context.reset();
        initializer.reset();
    }

    void TearDown() override
    {
        // Clean up after each test
        taint_engine_context.reset();
        initializer.reset();
    }
};

TEST_F(InitializeNativeStateTest, GlobalsAreNull_BeforeInitialization)
{
    EXPECT_EQ(taint_engine_context, nullptr);
    EXPECT_EQ(initializer, nullptr);
}

TEST_F(InitializeNativeStateTest, GlobalsAreInitialized_AfterInitialization)
{
    // Initialize
    initializer = std::make_unique<Initializer>();
    taint_engine_context = std::make_unique<TaintEngineContext>();

    EXPECT_NE(taint_engine_context, nullptr);
    EXPECT_NE(initializer, nullptr);
}

TEST_F(InitializeNativeStateTest, CanCreateRequestContext_AfterInitialization)
{
    // Initialize
    initializer = std::make_unique<Initializer>();
    taint_engine_context = std::make_unique<TaintEngineContext>();

    auto idx_opt = taint_engine_context->start_request_context();
    EXPECT_TRUE(idx_opt.has_value());
}

TEST_F(InitializeNativeStateTest, SafeWrappersReturnNull_BeforeInitialization)
{
    // Verify safe wrappers handle nullptr gracefully
    py::str test_str("test");

    EXPECT_EQ(safe_get_tainted_object_map(test_str.ptr()), nullptr);
    EXPECT_EQ(safe_get_tainted_object_map_by_ctx_id(0), nullptr);

    Source src("test", "value", OriginType::PARAMETER);
    EXPECT_EQ(safe_allocate_taint_range(0, 10, src, 0), nullptr);
    EXPECT_EQ(safe_allocate_tainted_object(), nullptr);
}
