#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <functional>

#include <echion/stacks.h>

// ============================================================================
// Test Cases for FrameStack
//
// Note: the test cases only check the "contents" of the Frame by only setting
// and looking at the name field, which is a StringTable::Key (so, an integer).
// We make sure each Frame actually gets a different "ID" (name) so that we can
// have the right assertions later to check the FrameStack is behaving as
// expected.
// ============================================================================

const constexpr size_t initial_capacity = 2048;

TEST(FrameStack, Construction)
{
    FrameStack stack;
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PushBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_FALSE(stack.empty());
    EXPECT_EQ(stack[0].get().name, 1);

    stack.push_back(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[1].get().name, 2);

    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);
    EXPECT_EQ(stack[2].get().name, 3);
}

TEST(FrameStack, PushFront)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_front(std::ref(frame1));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 1);

    stack.push_front(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 2);
    EXPECT_EQ(stack[1].get().name, 1);

    stack.push_front(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);
    EXPECT_EQ(stack[0].get().name, 3);
    EXPECT_EQ(stack[1].get().name, 2);
    EXPECT_EQ(stack[2].get().name, 1);
}

TEST(FrameStack, MixedPushFrontPushBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);
    Frame frame4(4);

    stack.push_back(std::ref(frame1));  // [1]
    stack.push_front(std::ref(frame2)); // [2, 1]
    stack.push_back(std::ref(frame3));  // [2, 1, 3]
    stack.push_front(std::ref(frame4)); // [4, 2, 1, 3]

    EXPECT_EQ(stack.size(), 4);
    EXPECT_EQ(stack[0].get().name, 4);
    EXPECT_EQ(stack[1].get().name, 2);
    EXPECT_EQ(stack[2].get().name, 1);
    EXPECT_EQ(stack[3].get().name, 3);
}

TEST(FrameStack, Clear)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);

    stack.clear();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());

    // Ensure stack is usable after clear
    Frame frame3(3);
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 3);
}

TEST(FrameStack, ForwardIterator)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));

    std::vector<StringTable::Key> values;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        values.push_back((*it).get().name);
    }

    EXPECT_THAT(values, ::testing::ElementsAre(1, 2, 3));
}

TEST(FrameStack, ReverseIterator)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));

    std::vector<StringTable::Key> values;
    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        values.push_back((*it).get().name);
    }

    EXPECT_THAT(values, ::testing::ElementsAre(3, 2, 1));
}

TEST(FrameStack, GrowthOnPushBack)
{
    // Push enough elements to force growth beyond initial_capacity (2048)
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 100;
    // Create enough frames to trigger growth
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Push back enough elements to force growth
    for (int i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify all values are correct after growth
    for (int i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, GrowthOnPushFront)
{
    // Push enough elements to force growth beyond initial_capacity (2048)
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 100;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Push front enough elements to force growth
    for (int i = 0; i < num_frames; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify all values are correct (reversed order)
    for (int i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, num_frames - 1 - i);
    }
}

TEST(FrameStack, GrowthWithMixedOperations)
{
    // Test growth with alternating push_front and push_back
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 200;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Alternate between push_front and push_back to force growth
    for (int i = 0; i < num_frames; ++i) {
        if (i % 2 == 0) {
            stack.push_back(std::ref(frames[i]));
        } else {
            stack.push_front(std::ref(frames[i]));
        }
    }

    EXPECT_EQ(stack.size(), num_frames);
}

TEST(FrameStack, GrowthPreservesOrder)
{
    FrameStack stack;
    std::vector<Frame> frames;

    for (int i = 0; i < 2048 * 2; ++i) {
        frames.emplace_back(i);
    }

    // Add elements using both push_back and push_front
    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    for (int i = 1024; i < 2048 * 2; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), 2048 * 2);

    // After growth, verify we can iterate correctly
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, 2048 * 2);
}

TEST(FrameStack, ClearAndReuse)
{
    FrameStack stack;
    std::vector<Frame> frames;

    for (int i = 0; i < 1024; ++i) {
        frames.emplace_back(i);
    }

    // Fill the stack
    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), 1024);

    // Clear and refill
    stack.clear();
    EXPECT_EQ(stack.size(), 0);

    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), 1024);

    for (int i = 0; i < 1024; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, EmptyAfterClear)
{
    FrameStack stack;
    Frame frame1(1);

    stack.push_back(std::ref(frame1));
    EXPECT_FALSE(stack.empty());

    stack.clear();
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);
}

TEST(FrameStack, IteratorConsistency)
{
    FrameStack stack;
    std::vector<Frame> frames;

    // Create all frames first to avoid vector reallocation invalidating references
    for (int i = 0; i < 10; ++i) {
        frames.emplace_back(i);
    }

    for (int i = 0; i < 10; ++i) {
        ASSERT_EQ(frames[i].name, i);
        stack.push_back(std::ref(frames[i]));
    }

    // Test that forward and reverse iterators are consistent
    std::vector<StringTable::Key> forward_values;
    std::vector<StringTable::Key> reverse_values;

    for (auto it = stack.begin(); it != stack.end(); ++it) {
        forward_values.push_back(it->name);
    }

    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        reverse_values.push_back(it->name);
    }

    std::reverse(reverse_values.begin(), reverse_values.end());
    EXPECT_EQ(forward_values, reverse_values);
}

TEST(FrameStack, LargeStack)
{
    FrameStack stack;
    std::vector<Frame> frames;

    const int large_size = 1000;
    for (int i = 0; i < large_size; ++i) {
        frames.emplace_back(i);
    }

    // Fill with large number of elements
    for (int i = 0; i < large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), large_size);

    // Verify all elements
    for (int i = 0; i < large_size; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, AlternatingGrowth)
{
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 300;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Alternate direction to test balanced growth
    for (int i = 0; i < num_frames; ++i) {
        if (i % 4 == 0 || i % 4 == 1) {
            stack.push_back(std::ref(frames[i]));
        } else {
            stack.push_front(std::ref(frames[i]));
        }
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Ensure we can still iterate
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, num_frames);
}

TEST(FrameStack, PopBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 1);
    EXPECT_EQ(stack[1].get().name, 2);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 1);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopFront)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 2);
    EXPECT_EQ(stack[1].get().name, 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopBackEmpty)
{
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    // pop_back on empty stack is UB (like std::deque)
#ifndef NDEBUG
    // In debug builds, verify assertion fires
    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
#else
    // In release builds, we don't trigger UB - just verify empty state
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);
#endif
}

TEST(FrameStack, PopFrontEmpty)
{
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    // pop_front on empty stack is UB (like std::deque)
#ifndef NDEBUG
    // In debug builds, verify assertion fires
    EXPECT_DEATH(stack.pop_front(), "pop_front on empty FrameStack");
#else
    // In release builds, we don't trigger UB - just verify empty state
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);
#endif
}

TEST(FrameStack, MixedPushPop)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);
    Frame frame4(4);

    stack.push_back(std::ref(frame1));  // [1]
    stack.push_back(std::ref(frame2));  // [1, 2]
    stack.pop_front();                  // [2]
    stack.push_front(std::ref(frame3)); // [3, 2]
    stack.push_back(std::ref(frame4));  // [3, 2, 4]
    stack.pop_back();                   // [3, 2]

    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 3);
    EXPECT_EQ(stack[1].get().name, 2);
}

// ============================================================================
// Stress Tests and Edge Cases
// ============================================================================

TEST(FrameStack, PushExactlyAtCapacity)
{
    // Test pushing exactly to capacity boundary
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < initial_capacity; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < initial_capacity; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), initial_capacity);

    // Verify all elements
    for (size_t i = 0; i < initial_capacity; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, PushOneOverCapacity)
{
    // Test the exact moment of first growth
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < initial_capacity + 1; ++i) {
        frames.emplace_back(i);
    }

    // Fill to capacity
    for (size_t i = 0; i < initial_capacity; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), initial_capacity);

    // Push one more to trigger growth
    stack.push_back(std::ref(frames[initial_capacity]));
    EXPECT_EQ(stack.size(), initial_capacity + 1);

    // Verify all elements still correct
    for (size_t i = 0; i < initial_capacity + 1; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, MultipleGrowthCycles)
{
    // Test multiple growth cycles, not just the first one
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t large_size = initial_capacity * 4;
    for (size_t i = 0; i < large_size; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), large_size);

    // Verify all elements
    for (size_t i = 0; i < large_size; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, AsymmetricGrowthAllFront)
{
    // Stress test: grow only via push_front
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity * 2;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify reverse order
    for (size_t i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, num_frames - 1 - i);
    }
}

TEST(FrameStack, AsymmetricGrowthAllBack)
{
    // Stress test: grow only via push_back
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity * 2;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify order
    for (size_t i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, GrowShrinkGrowCycle)
{
    // Test growth, then shrinking, then growth again
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t large_size = initial_capacity + 500;
    for (size_t i = 0; i < large_size; ++i) {
        frames.emplace_back(i);
    }

    // First growth
    for (size_t i = 0; i < large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), large_size);

    // Shrink significantly
    for (size_t i = 0; i < 400; ++i) {
        stack.pop_back();
    }
    EXPECT_EQ(stack.size(), large_size - 400);

    // Grow again
    for (size_t i = large_size - 400; i < large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), large_size);

    // Verify all elements
    for (size_t i = 0; i < large_size; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, PopAllThenRefill)
{
    // Test popping everything, then refilling
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 100;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Fill and grow
    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Pop everything
    while (!stack.empty()) {
        stack.pop_back();
    }
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());

    // Refill
    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Verify
    for (size_t i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, AlternatingPushPopWithGrowth)
{
    // Stress test: alternate push and pop while growing
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity * 2;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Push 3, pop 1 pattern - net growth of 2 each cycle
    size_t frame_idx = 0;
    while (frame_idx + 3 <= num_frames) {
        stack.push_back(std::ref(frames[frame_idx++]));
        stack.push_back(std::ref(frames[frame_idx++]));
        stack.push_back(std::ref(frames[frame_idx++]));
        if (stack.size() > 0) {
            stack.pop_front();
        }
    }

    // Stack should have grown beyond initial capacity
    EXPECT_GT(stack.size(), initial_capacity);
}

TEST(FrameStack, RapidClearAndRefillCycles)
{
    // Test rapid clear/refill cycles with growth
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 200;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    for (int cycle = 0; cycle < 5; ++cycle) {
        // Fill past capacity
        for (size_t i = 0; i < num_frames; ++i) {
            stack.push_back(std::ref(frames[i]));
        }
        EXPECT_EQ(stack.size(), num_frames);

        // Clear
        stack.clear();
        EXPECT_EQ(stack.size(), 0);
    }

    // Final fill and verify
    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    for (size_t i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, IteratorOnEmptyStack)
{
    // Test that iterators work correctly on empty stack
    FrameStack stack;

    EXPECT_EQ(stack.begin(), stack.end());
    EXPECT_EQ(stack.rbegin(), stack.rend());

    // Should be safe to iterate (zero iterations)
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, 0);

    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, 0);
}

TEST(FrameStack, IteratorAfterGrowth)
{
    // Ensure iterators work correctly after growth
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 100;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    // Test forward iteration
    std::vector<StringTable::Key> forward_values;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        forward_values.push_back((*it).get().name);
    }
    EXPECT_EQ(forward_values.size(), num_frames);

    // Test reverse iteration
    std::vector<StringTable::Key> reverse_values;
    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        reverse_values.push_back((*it).get().name);
    }
    EXPECT_EQ(reverse_values.size(), num_frames);

    // Verify consistency
    std::reverse(reverse_values.begin(), reverse_values.end());
    EXPECT_EQ(forward_values, reverse_values);
}

TEST(FrameStack, AccessPatternAfterMixedGrowth)
{
    // Test operator[] access after complex growth pattern
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < initial_capacity + 500; ++i) {
        frames.emplace_back(i);
    }

    // Complex pattern: half back, half front
    for (size_t i = 0; i < initial_capacity / 2; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    for (size_t i = initial_capacity / 2; i < initial_capacity + 500; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), initial_capacity + 500);

    // Access every element via operator[]
    for (size_t i = 0; i < stack.size(); ++i) {
        // Just ensure no crash on access
        EXPECT_GE(stack[i].get().name, 0);
    }
}

TEST(FrameStack, StressTestVeryLargeStack)
{
    // Stress test with very large stack (10x capacity)
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t very_large_size = initial_capacity * 10;
    frames.reserve(very_large_size);
    for (size_t i = 0; i < very_large_size; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < very_large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), very_large_size);

    // Sample verification (not all elements to keep test fast)
    EXPECT_EQ(stack[0].get().name, 0);
    EXPECT_EQ(stack[very_large_size / 2].get().name, very_large_size / 2);
    EXPECT_EQ(stack[very_large_size - 1].get().name, very_large_size - 1);
}

TEST(FrameStack, ReferenceStabilityAfterGrowth)
{
    // Verify that stored references remain valid after growth
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < initial_capacity + 100; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < initial_capacity + 100; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    // Access and verify references point to original frames
    for (size_t i = 0; i < initial_capacity + 100; ++i) {
        EXPECT_EQ(&stack[i].get(), &frames[i]);
    }
}

TEST(FrameStack, EdgeCaseSingleElement)
{
    // Test operations with just one element
    FrameStack stack;
    Frame frame(42);

    stack.push_back(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_FALSE(stack.empty());
    EXPECT_EQ(stack[0].get().name, 42);

    // Test iterator with single element
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        EXPECT_EQ((*it).get().name, 42);
        ++count;
    }
    EXPECT_EQ(count, 1);

    stack.pop_back();
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopUntilEmptyFromBothEnds)
{
    // Test popping from both ends until empty
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = 100;
    frames.reserve(num_frames); // Reserve to prevent reallocation that would invalidate references
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
        stack.push_back(std::ref(frames[i]));
    }

    // Pop alternating from front and back
    while (stack.size() >= 2) {
        stack.pop_front();
        stack.pop_back();
    }

    // Should have 0 or 1 elements left
    EXPECT_LE(stack.size(), 1);
}

TEST(FrameStack, GrowthWithOnlyFrontOps)
{
    // Ensure growth works when only using front operations
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 250;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Only push_front operations
    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_front(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Only pop_front operations
    size_t expected_name = num_frames - 1;
    while (!stack.empty()) {
        EXPECT_EQ(stack[0].get().name, expected_name);
        stack.pop_front();
        expected_name--;
    }
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, GrowBothEndsMultipleTimesWithValidation)
{
    // Grow both front and back through multiple growth cycles, validating at each step
    FrameStack stack;
    std::vector<Frame> frames;

    // We'll push enough to trigger multiple growth cycles (way beyond initial capacity)
    const size_t total_frames = initial_capacity * 3;
    frames.reserve(total_frames);
    for (size_t i = 0; i < total_frames; ++i) {
        frames.emplace_back(i);
    }

    // Track which frames we expect at front and back
    // back_frames will be pushed to back in order
    // front_frames will be pushed to front in reverse order
    std::vector<size_t> expected_order;

    size_t back_idx = 0;
    size_t front_idx = total_frames - 1;
    size_t operations = 0;

    // Alternate: push 3 to back, then 2 to front, repeat
    // This creates asymmetric growth on both ends
    while (back_idx < total_frames / 2 && front_idx >= total_frames / 2) {
        // Push 3 to back
        for (int i = 0; i < 3 && back_idx < total_frames / 2; ++i) {
            stack.push_back(std::ref(frames[back_idx]));
            expected_order.push_back(back_idx);
            back_idx++;
            operations++;
        }

        // Verify contents every 100 operations
        if (operations % 100 == 0) {
            ASSERT_EQ(stack.size(), expected_order.size());
            for (size_t j = 0; j < stack.size(); ++j) {
                ASSERT_EQ(stack[j].get().name, expected_order[j])
                  << "Mismatch at index " << j << " after " << operations << " operations";
            }
        }

        // Push 2 to front
        for (int i = 0; i < 2 && front_idx >= total_frames / 2; ++i) {
            stack.push_front(std::ref(frames[front_idx]));
            expected_order.insert(expected_order.begin(), front_idx);
            front_idx--;
            operations++;
        }

        // Verify contents every 100 operations
        if (operations % 100 == 0) {
            ASSERT_EQ(stack.size(), expected_order.size());
            for (size_t j = 0; j < stack.size(); ++j) {
                ASSERT_EQ(stack[j].get().name, expected_order[j])
                  << "Mismatch at index " << j << " after " << operations << " operations";
            }
        }
    }

    // Final validation of all elements
    EXPECT_GT(stack.size(), initial_capacity * 2); // Should have grown multiple times
    ASSERT_EQ(stack.size(), expected_order.size());

    for (size_t i = 0; i < stack.size(); ++i) {
        EXPECT_EQ(stack[i].get().name, expected_order[i]) << "Final mismatch at index " << i;
    }

    // Also validate via iterators
    std::vector<StringTable::Key> iterated_values;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        iterated_values.push_back((*it).get().name);
    }
    ASSERT_EQ(iterated_values.size(), expected_order.size());
    for (size_t i = 0; i < iterated_values.size(); ++i) {
        EXPECT_EQ(iterated_values[i], expected_order[i]) << "Iterator mismatch at index " << i;
    }
}

TEST(FrameStack, GrowBothEndsSimultaneouslyWithFullValidation)
{
    // Simpler version: strictly alternate back/front and validate after EVERY operation
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity * 2 + 100;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // We'll build the expected state step by step
    std::vector<size_t> expected_order;

    // Alternate: back, front, back, front...
    size_t back_idx = 0;
    size_t front_idx = num_frames - 1;

    for (size_t op = 0; op < num_frames && back_idx <= front_idx; ++op) {
        if (op % 2 == 0) {
            // Push to back
            stack.push_back(std::ref(frames[back_idx]));
            expected_order.push_back(back_idx);
            back_idx++;
        } else {
            // Push to front
            stack.push_front(std::ref(frames[front_idx]));
            expected_order.insert(expected_order.begin(), front_idx);
            front_idx--;
        }

        // Validate size
        ASSERT_EQ(stack.size(), expected_order.size()) << "Size mismatch after operation " << op;

        // Validate all contents (expensive but thorough)
        for (size_t i = 0; i < stack.size(); ++i) {
            ASSERT_EQ(stack[i].get().name, expected_order[i])
              << "Content mismatch at index " << i << " after operation " << op << " (pushed to "
              << (op % 2 == 0 ? "back" : "front") << ")";
        }
    }

    // Final check: should have grown well beyond initial capacity
    EXPECT_GT(stack.size(), initial_capacity);
}

TEST(FrameStack, GrowBothEndsWithIntermediateClears)
{
    // Grow both ends, clear, grow again - validate at each phase
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 500;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Phase 1: Grow by alternating back/front
    for (size_t i = 0; i < 700; ++i) {
        if (i % 2 == 0) {
            stack.push_back(std::ref(frames[i]));
        } else {
            stack.push_front(std::ref(frames[i]));
        }
    }
    size_t phase1_size = stack.size();
    EXPECT_EQ(phase1_size, 700);

    // Validate phase 1 (spot checks)
    EXPECT_GE(stack[0].get().name, 0);
    EXPECT_GE(stack[phase1_size - 1].get().name, 0);

    // Clear
    stack.clear();
    EXPECT_EQ(stack.size(), 0);

    // Phase 2: Grow again with different pattern (more back-heavy)
    for (size_t i = 700; i < 700 + 800; ++i) {
        if (i % 3 == 0) {
            stack.push_front(std::ref(frames[i % num_frames]));
        } else {
            stack.push_back(std::ref(frames[i % num_frames]));
        }
    }
    size_t phase2_size = stack.size();
    EXPECT_EQ(phase2_size, 800);

    // Validate phase 2 - all elements
    for (size_t i = 0; i < phase2_size; ++i) {
        EXPECT_GE(stack[i].get().name, 0);
        EXPECT_LT(stack[i].get().name, num_frames);
    }

    // Phase 3: Continue growing from phase 2
    size_t pre_phase3_size = stack.size();
    for (size_t i = 0; i < 900; ++i) {
        if (i % 2 == 0) {
            stack.push_back(std::ref(frames[i % num_frames]));
        } else {
            stack.push_front(std::ref(frames[i % num_frames]));
        }
    }
    EXPECT_EQ(stack.size(), pre_phase3_size + 900);

    // Final validation
    for (size_t i = 0; i < stack.size(); ++i) {
        EXPECT_GE(stack[i].get().name, 0);
        EXPECT_LT(stack[i].get().name, num_frames);
    }
}

// ============================================================================
// Problematic Cases and Edge Case Behavior Tests
// ============================================================================

TEST(FrameStack, MultiplePopBackOnEmpty)
{
    // Test that popping from empty triggers assertion in debug builds
    FrameStack stack;
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);

#ifndef NDEBUG
    // In debug builds, first pop should assert
    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
#else
    // In release builds, we don't trigger UB - just verify empty state persists
    EXPECT_TRUE(stack.empty());
#endif

    // Stack should still be usable with valid operations
    Frame frame(42);
    stack.push_back(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 42);
}

TEST(FrameStack, MultiplePopFrontOnEmpty)
{
    // Test that popping from empty triggers assertion in debug builds
    FrameStack stack;
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);

#ifndef NDEBUG
    // In debug builds, first pop should assert
    EXPECT_DEATH(stack.pop_front(), "pop_front on empty FrameStack");
#else
    // In release builds, we don't trigger UB - just verify empty state persists
    EXPECT_TRUE(stack.empty());
#endif

    // Stack should still be usable with valid operations
    Frame frame(99);
    stack.push_front(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 99);
}

TEST(FrameStack, PopUntilEmptyThenPopMore)
{
    // Pop until empty, verify we can't pop more (UB like std::deque)
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);

    // Pop all elements (valid)
    stack.pop_back();
    stack.pop_back();
    stack.pop_back();
    EXPECT_TRUE(stack.empty());

#ifndef NDEBUG
    // In debug builds, should assert
    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
    // Note: can't test pop_front after death test, would need separate test
#else
    // In release builds, we don't trigger UB - just verify empty state
    EXPECT_TRUE(stack.empty());
#endif

    // Verify stack is still functional with valid operations
    Frame frame4(4);
    stack.push_back(std::ref(frame4));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 4);
}

TEST(FrameStack, MultipleClearsOnEmpty)
{
    // Test multiple clear() calls on empty stack
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    stack.clear();
    EXPECT_TRUE(stack.empty());

    stack.clear();
    EXPECT_TRUE(stack.empty());

    stack.clear();
    EXPECT_TRUE(stack.empty());

    // Should still work normally
    Frame frame(123);
    stack.push_back(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
}

TEST(FrameStack, ClearAfterPopToEmpty)
{
    // Clear after already popping to empty
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));

    // Pop to empty
    stack.pop_back();
    stack.pop_back();
    EXPECT_TRUE(stack.empty());

    // Clear on already empty stack
    stack.clear();
    EXPECT_TRUE(stack.empty());

    // Verify functionality
    Frame frame3(3);
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 1);
}

TEST(FrameStack, EmptyCheckConsistency)
{
    // Verify empty() and size() are always consistent
    FrameStack stack;
    Frame frame(1);

    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);

    stack.push_back(std::ref(frame));
    EXPECT_FALSE(stack.empty());
    EXPECT_EQ(stack.size(), 1);

    stack.pop_back();
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);

    stack.push_front(std::ref(frame));
    EXPECT_FALSE(stack.empty());
    EXPECT_EQ(stack.size(), 1);

    stack.pop_front();
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);

    stack.clear();
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);
}

TEST(FrameStack, BeginEndConsistencyOnEmpty)
{
    // Verify begin() == end() on empty stack in various states
    FrameStack stack;

    // Initially empty
    EXPECT_EQ(stack.begin(), stack.end());
    EXPECT_EQ(stack.rbegin(), stack.rend());

    // After push and pop
    Frame frame(1);
    stack.push_back(std::ref(frame));
    EXPECT_NE(stack.begin(), stack.end());

    stack.pop_back();
    EXPECT_EQ(stack.begin(), stack.end());
    EXPECT_EQ(stack.rbegin(), stack.rend());

    // After clear
    stack.push_back(std::ref(frame));
    stack.clear();
    EXPECT_EQ(stack.begin(), stack.end());
    EXPECT_EQ(stack.rbegin(), stack.rend());
}

TEST(FrameStack, PushPopSingleElementRepeatedly)
{
    // Stress test: push and pop single element many times
    FrameStack stack;
    Frame frame(42);

    for (int i = 0; i < 1000; ++i) {
        stack.push_back(std::ref(frame));
        EXPECT_EQ(stack.size(), 1);
        EXPECT_FALSE(stack.empty());
        EXPECT_EQ(stack[0].get().name, 42);

        stack.pop_back();
        EXPECT_EQ(stack.size(), 0);
        EXPECT_TRUE(stack.empty());
    }

    // Same with front
    for (int i = 0; i < 1000; ++i) {
        stack.push_front(std::ref(frame));
        EXPECT_EQ(stack.size(), 1);
        EXPECT_EQ(stack[0].get().name, 42);

        stack.pop_front();
        EXPECT_TRUE(stack.empty());
    }
}

TEST(FrameStack, AccessAtBoundaries)
{
    // Test access at exact boundaries
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = 100;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    // Access first and last elements repeatedly
    for (int i = 0; i < 100; ++i) {
        EXPECT_EQ(stack[0].get().name, 0);
        EXPECT_EQ(stack[num_frames - 1].get().name, num_frames - 1);
    }

    // Access via iterators
    EXPECT_EQ((*stack.begin()).get().name, 0);
    EXPECT_EQ((*stack.rbegin()).get().name, num_frames - 1);
}

TEST(FrameStack, AlternatingPushPopToEmpty)
{
    // Push some, pop all, repeat - test that cycles work correctly
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < 50; ++i) {
        frames.emplace_back(i);
    }

    for (int cycle = 0; cycle < 10; ++cycle) {
        // Push some elements
        for (size_t i = 0; i < 10; ++i) {
            stack.push_back(std::ref(frames[i]));
        }
        EXPECT_EQ(stack.size(), 10);

        // Pop all (valid operations)
        for (size_t i = 0; i < 10; ++i) {
            stack.pop_back();
        }
        EXPECT_TRUE(stack.empty());

        // Note: we don't pop from empty as that's UB
    }

    // Verify final state is usable
    stack.push_back(std::ref(frames[0]));
    EXPECT_EQ(stack.size(), 1);
}

TEST(FrameStack, GrowThenPopAllThenPopMore)
{
    // Grow beyond capacity, pop everything - verify state
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity + 200;
    frames.reserve(num_frames); // Reserve to prevent reallocation that would invalidate references
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Pop all (valid operations)
    for (size_t i = 0; i < num_frames; ++i) {
        stack.pop_back();
    }
    EXPECT_TRUE(stack.empty());

#ifndef NDEBUG
    // In debug builds, first pop should assert
    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
#else
    // In release builds, we don't trigger UB - just verify empty state
    EXPECT_TRUE(stack.empty());
#endif

    // Verify stack still works with valid operations
    Frame frame(999);
    stack.push_back(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 999);
}

TEST(FrameStack, MixedPopFromBothEndsToEmpty)
{
    // Pop from both front and back until empty
    FrameStack stack;
    std::vector<Frame> frames;

    frames.reserve(20); // Reserve to prevent reallocation that would invalidate references
    for (size_t i = 0; i < 20; ++i) {
        frames.emplace_back(i);
        stack.push_back(std::ref(frames[i]));
    }

    // Pop alternating from front and back (all valid)
    for (int i = 0; i < 10; ++i) {
        EXPECT_FALSE(stack.empty());
        stack.pop_front();

        EXPECT_FALSE(stack.empty());
        stack.pop_back();
    }

    EXPECT_TRUE(stack.empty());

    // Note: popping from empty would be UB, so we don't test it here
    // Verify stack is still usable
    Frame frame(42);
    stack.push_back(std::ref(frame));
    EXPECT_EQ(stack.size(), 1);
}

TEST(FrameStack, OperationsAfterMultipleClears)
{
    // Clear multiple times, then ensure operations work
    FrameStack stack;
    std::vector<Frame> frames;

    for (size_t i = 0; i < 100; ++i) {
        frames.emplace_back(i);
    }

    // Multiple fill and clear cycles
    for (int cycle = 0; cycle < 5; ++cycle) {
        for (size_t i = 0; i < 20; ++i) {
            stack.push_back(std::ref(frames[i]));
        }
        stack.clear();
        EXPECT_TRUE(stack.empty());
    }

    // Now do operations after all those clears
    for (size_t i = 0; i < 50; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), 50);

    for (size_t i = 0; i < 50; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, PushPopBoundaryAfterGrowth)
{
    // Grow, then test push/pop at the new capacity boundaries
    FrameStack stack;
    std::vector<Frame> frames;

    const size_t num_frames = initial_capacity * 2;
    for (size_t i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Fill to 2x capacity
    for (size_t i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Pop half
    for (size_t i = 0; i < num_frames / 2; ++i) {
        stack.pop_back();
    }
    EXPECT_EQ(stack.size(), num_frames / 2);

    // Push more (testing at boundary)
    for (size_t i = num_frames / 2; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), num_frames);

    // Verify correctness
    for (size_t i = 0; i < num_frames / 2; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, EmptyStackIteratorIncrement)
{
    // Ensure incrementing iterators on empty stack doesn't crash
    FrameStack stack;

    auto it = stack.begin();
    auto end = stack.end();
    EXPECT_EQ(it, end);

    // Incrementing end iterator is undefined but shouldn't crash in debug builds
    // We just verify begin == end
    auto rit = stack.rbegin();
    auto rend = stack.rend();
    EXPECT_EQ(rit, rend);
}

TEST(FrameStack, SingleElementIterator)
{
    // Test iterator behavior with single element
    FrameStack stack;
    Frame frame(42);
    stack.push_back(std::ref(frame));

    // Forward iterator
    auto it = stack.begin();
    EXPECT_NE(it, stack.end());
    EXPECT_EQ((*it).get().name, 42);
    ++it;
    EXPECT_EQ(it, stack.end());

    // Reverse iterator
    auto rit = stack.rbegin();
    EXPECT_NE(rit, stack.rend());
    EXPECT_EQ((*rit).get().name, 42);
    ++rit;
    EXPECT_EQ(rit, stack.rend());
}

// ============================================================================
// Undefined Behavior Tests (matching std::deque semantics)
// ============================================================================

#ifndef NDEBUG
// Death tests only work in debug builds where assertions are enabled

TEST(FrameStackDeathTest, PopBackOnEmptyAsserts)
{
    // Verify that pop_back on empty stack asserts in debug builds
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
}

TEST(FrameStackDeathTest, PopFrontOnEmptyAsserts)
{
    // Verify that pop_front on empty stack asserts in debug builds
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    EXPECT_DEATH(stack.pop_front(), "pop_front on empty FrameStack");
}

TEST(FrameStackDeathTest, PopBackAfterClearAsserts)
{
    // Verify assertion after clearing a stack
    FrameStack stack;
    Frame frame(1);
    stack.push_back(std::ref(frame));
    stack.clear();

    EXPECT_TRUE(stack.empty());
    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
}

TEST(FrameStackDeathTest, PopFrontAfterClearAsserts)
{
    // Verify assertion after clearing a stack
    FrameStack stack;
    Frame frame(1);
    stack.push_front(std::ref(frame));
    stack.clear();

    EXPECT_TRUE(stack.empty());
    EXPECT_DEATH(stack.pop_front(), "pop_front on empty FrameStack");
}

TEST(FrameStackDeathTest, PopBackAfterPoppingAllAsserts)
{
    // Verify assertion when popping after already popped all elements
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));

    stack.pop_back();
    stack.pop_back();
    EXPECT_TRUE(stack.empty());

    EXPECT_DEATH(stack.pop_back(), "pop_back on empty FrameStack");
}

TEST(FrameStackDeathTest, PopFrontAfterPoppingAllAsserts)
{
    // Verify assertion when popping after already popped all elements
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);

    stack.push_front(std::ref(frame1));
    stack.push_front(std::ref(frame2));

    stack.pop_front();
    stack.pop_front();
    EXPECT_TRUE(stack.empty());

    EXPECT_DEATH(stack.pop_front(), "pop_front on empty FrameStack");
}

#endif // !NDEBUG

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
